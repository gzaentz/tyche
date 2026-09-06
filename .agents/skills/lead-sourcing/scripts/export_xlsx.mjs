#!/usr/bin/env node

/** Export accepted TYCHE company-contact pairs to a styled Excel workbook. */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

export const XLSX_COLUMNS = [
  "Name",
  "Email",
  "Role",
  "Company",
  "LinkedIn",
  "Website",
  "Company LinkedIn",
  "Industry",
  "Sub Industry",
  "City",
  "State",
  "Country",
  "HQ State",
  "HQ Country",
  "Employee Count",
  "Description",
  "Intent Details",
  "Phone",
];

const COLUMN_LETTERS = [
  "A", "B", "C", "D", "E", "F", "G", "H", "I",
  "J", "K", "L", "M", "N", "O", "P", "Q", "R",
];

const COLUMN_WIDTHS = [
  24, 28, 38, 26, 44, 30, 40, 20, 28,
  18, 18, 18, 18, 18, 16, 48, 72, 20,
];

export class ExportError extends Error {}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

function isLinkedInUrl(value) {
  if (!value) return false;
  try {
    const host = new URL(value).hostname.toLowerCase();
    return host === "linkedin.com" || host.endsWith(".linkedin.com");
  } catch {
    return false;
  }
}

function contactLinkedIn(contact) {
  const explicit = text(contact.linkedin_url);
  if (explicit) return explicit;
  const contactUrl = text(contact.contact_url);
  return isLinkedInUrl(contactUrl) ? contactUrl : "";
}

function website(company) {
  const explicit = text(company.website);
  if (explicit) return explicit;
  const domain = text(company.domain);
  if (!domain) return "";
  if (domain.startsWith("http://") || domain.startsWith("https://")) return domain;
  return `https://${domain.replace(/^\/+/, "")}`;
}

function employeeCount(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const normalized = text(value);
  if (/^\d+$/.test(normalized)) return Number(normalized);
  return normalized;
}

function intentDetails(signal) {
  const parts = [
    ["Signal", signal.signal],
    ["Date", signal.evidence_date],
    ["Details", signal.evidence_text],
    ["Source", signal.evidence_url],
  ];
  return parts
    .map(([label, value]) => [label, text(value)])
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${value}`)
    .join("; ");
}

function requestedContactFields(document) {
  const request = object(document.request);
  const fields = request.contact_fields;
  if (fields === undefined) return new Set(["email"]);
  if (!Array.isArray(fields)) {
    throw new ExportError("results.json request.contact_fields must be an array");
  }
  return new Set(fields);
}

function requestedValue(contact, field, requestedFields, index) {
  if (!requestedFields.has(field)) return "";
  const value = text(contact[field]);
  if (!value) {
    throw new ExportError(
      `accepted[${index}].primary_contact.${field} is required because contact_fields requests it`,
    );
  }
  if (field === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    throw new ExportError(`accepted[${index}].primary_contact.email is invalid`);
  }
  return value;
}

function validateEmailReceipt(document, contact, email, index, validator = "zerobounce", path = `accepted[${index}].primary_contact.email_validation`) {
  const receipt = object(contact.email_validation);
  if (!Object.keys(receipt).length) {
    throw new ExportError(`${path} requires a Deepline ZeroBounce receipt`);
  }
  if (text(receipt.email).toLowerCase() !== email.toLowerCase()) {
    throw new ExportError(`${path}.email must match the contact email`);
  }
  const status = text(receipt.status);
  if (!status) {
    throw new ExportError(`${path}.status is unresolved or missing`);
  }
  const fallback = receipt.fallback;
  if (validator === "bounceban") {
    if (status.toLowerCase() !== "success" || text(receipt.result).toLowerCase() !== "deliverable") {
      throw new ExportError(`${path} requires BounceBan success and result deliverable`);
    }
    if ("fallback" in receipt) throw new ExportError(`${path} cannot chain fallbacks`);
  } else if (["catch-all", "unknown"].includes(status.toLowerCase()) && Object.keys(object(fallback)).length) {
    validateEmailReceipt(document, { email_validation: fallback }, email, index, "bounceban", `${path}.fallback`);
    const ids = (document.routes || []).map((route) => route.route_id);
    if (ids.indexOf(object(fallback.source).route_id) <= ids.indexOf(object(receipt.source).route_id)) {
      throw new ExportError(`${path} fallback must use a distinct later route`);
    }
  } else if (status.toLowerCase() !== "valid") {
    throw new ExportError(`${path}.status must be valid`);
  }
  if (validator === "zerobounce" && "fallback" in receipt && !["catch-all", "unknown"].includes(status.toLowerCase())) {
    throw new ExportError(`${path} fallback is only allowed for catch-all or unknown`);
  }

  const source = object(receipt.source);
  if (text(source.provider).toLowerCase() !== "deepline") {
    throw new ExportError(`${path}.source.provider must be deepline`);
  }
  if (text(source.validator).toLowerCase() !== validator) {
    throw new ExportError(`${path}.source.validator must be ${validator}`);
  }
  if (text(source.operation) !== "execute") {
    throw new ExportError(`${path}.source.operation must be execute`);
  }
  const tool = text(source.tool);
  const routeId = text(source.route_id);
  if (!tool || !routeId) {
    throw new ExportError(`${path}.source requires tool and route_id`);
  }

  const routes = Array.isArray(document.routes) ? document.routes : [];
  const matchingRoutes = routes.filter(
    (candidate) => text(object(candidate).route_id) === routeId,
  );
  if (matchingRoutes.length !== 1) {
    throw new ExportError(`${path}.source.route_id must identify one route receipt`);
  }
  const route = object(matchingRoutes[0]);
  if (
    route.provider !== "deepline"
    || route.phase !== "email_validation"
    || route.operation !== "execute"
    || route.tool !== tool
    || !["ok", "partial"].includes(route.provider_status)
    || !Number.isInteger(route.paid_calls)
    || route.paid_calls < 1
    || (validator === "bounceban" && route.paid_calls !== 1)
  ) {
    throw new ExportError(`${path} does not match a successful paid Deepline validation route`);
  }
}

export function rowsFor(document) {
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new ExportError("results.json must contain one JSON object");
  }
  if (!Array.isArray(document.accepted)) {
    throw new ExportError("results.json accepted must be an array");
  }

  const requestedFields = requestedContactFields(document);
  return document.accepted.map((acceptedRow, index) => {
    if (!acceptedRow || typeof acceptedRow !== "object" || Array.isArray(acceptedRow)) {
      throw new ExportError(`accepted[${index}] must be an object`);
    }
    const company = object(acceptedRow.company);
    const contact = object(acceptedRow.primary_contact);
    const signal = object(acceptedRow.signal_evidence);
    if (!Object.keys(company).length || !Object.keys(contact).length) {
      throw new ExportError(
        `accepted[${index}] requires company and primary_contact objects`,
      );
    }

    const requiredValues = {
      Name: text(contact.full_name),
      Role: text(contact.current_title),
      Company: text(company.canonical_name),
    };
    for (const [label, value] of Object.entries(requiredValues)) {
      if (!value) throw new ExportError(`accepted[${index}] requires ${label}`);
    }

    const email = requestedValue(contact, "email", requestedFields, index);
    const phone = requestedValue(contact, "phone", requestedFields, index);
    if (email) validateEmailReceipt(document, contact, email, index);
    for (const [backupIndex, backup] of (acceptedRow.backup_contacts || []).entries()) {
      const backupEmail = text(object(backup).email);
      if (backupEmail) validateEmailReceipt(document, backup, backupEmail, index, "zerobounce", `accepted[${index}].backup_contacts[${backupIndex}].email_validation`);
    }

    return {
      Name: requiredValues.Name,
      Email: email,
      Role: requiredValues.Role,
      Company: requiredValues.Company,
      LinkedIn: contactLinkedIn(contact),
      Website: website(company),
      "Company LinkedIn": text(company.linkedin_url),
      Industry: text(company.industry),
      "Sub Industry": text(company.sub_industry),
      City: text(contact.city),
      State: text(contact.state),
      Country: text(contact.country),
      "HQ State": text(company.hq_state),
      "HQ Country": text(company.hq_country),
      "Employee Count": employeeCount(company.employee_count),
      Description: text(company.description),
      "Intent Details": intentDetails(signal),
      Phone: phone,
    };
  });
}

function matrixFor(rows) {
  return [
    XLSX_COLUMNS,
    ...rows.map((row) => XLSX_COLUMNS.map((column) => row[column] === "" ? null : row[column])),
  ];
}

async function loadArtifactTool(nodeModulesPath) {
  if (!nodeModulesPath) {
    throw new ExportError("--node-modules is required for the Codex workbook runtime");
  }
  const absoluteNodeModules = path.resolve(nodeModulesPath);
  const resolver = createRequire(path.join(path.dirname(absoluteNodeModules), "package.json"));
  const entry = resolver.resolve("@oai/artifact-tool");
  return import(pathToFileURL(entry).href);
}

function inspectionText(result) {
  if (result && typeof result.ndjson === "string") return result.ndjson;
  if (typeof result === "string") return result;
  return JSON.stringify(result ?? null);
}

export async function exportXlsx(document, destination, options = {}) {
  const rows = rowsFor(document);
  const { Workbook, SpreadsheetFile } = await loadArtifactTool(options.nodeModules);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("Leads");
  const lastRow = rows.length + 1;
  const usedRangeAddress = `A1:R${lastRow}`;

  sheet.getRange(usedRangeAddress).values = matrixFor(rows);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(4);

  const header = sheet.getRange("A1:R1");
  header.format = {
    fill: "#0F766E",
    font: { bold: true, color: "#FFFFFF", name: "Aptos", size: 10 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
    wrapText: true,
    borders: { bottom: { style: "medium", color: "#115E59" } },
    rowHeight: 30,
  };

  if (rows.length) {
    const body = sheet.getRange(`A2:R${lastRow}`);
    body.format = {
      font: { color: "#1F2937", name: "Aptos", size: 10 },
      verticalAlignment: "top",
      rowHeight: 66,
    };
    sheet.getRange(`C2:C${lastRow}`).format.wrapText = true;
    sheet.getRange(`P2:Q${lastRow}`).format.wrapText = true;
    sheet.getRange(`O2:O${lastRow}`).format.numberFormat = "#,##0";

    const table = sheet.tables.add(usedRangeAddress, true, "LeadsTable");
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }

  COLUMN_LETTERS.forEach((column, index) => {
    sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = COLUMN_WIDTHS[index];
  });

  const regionInspection = await workbook.inspect({
    kind: "region",
    sheetId: "Leads",
    range: usedRangeAddress,
    maxChars: 12000,
  });
  const formulaInspection = await workbook.inspect({
    kind: "formula",
    sheetId: "Leads",
    range: usedRangeAddress,
    maxChars: 4000,
    options: { maxResults: 50 },
  });
  const errorInspection = await workbook.inspect({
    kind: "match",
    sheetId: "Leads",
    range: usedRangeAddress,
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    maxChars: 4000,
    options: { useRegex: true, maxResults: 50 },
  });

  if (options.preview) {
    await fs.mkdir(path.dirname(path.resolve(options.preview)), { recursive: true });
    const preview = await workbook.render({
      sheetName: "Leads",
      autoCrop: "all",
      scale: 1,
      format: "png",
    });
    await fs.writeFile(
      options.preview,
      new Uint8Array(await preview.arrayBuffer()),
    );
  }

  await fs.mkdir(path.dirname(path.resolve(destination)), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  const temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "tyche-xlsx-"));
  try {
    const temporaryWorkbook = path.join(temporaryDirectory, "leads.xlsx");
    await output.save(temporaryWorkbook);
    await fs.copyFile(temporaryWorkbook, destination);
  } finally {
    await fs.rm(temporaryDirectory, { recursive: true, force: true });
  }

  const inspection = {
    used_range: usedRangeAddress,
    region: inspectionText(regionInspection),
    formulas: inspectionText(formulaInspection),
    formula_errors: inspectionText(errorInspection),
  };
  if (options.inspection) {
    await fs.mkdir(path.dirname(path.resolve(options.inspection)), { recursive: true });
    await fs.writeFile(options.inspection, `${JSON.stringify(inspection, null, 2)}\n`);
  }

  return { rows: rows.length, columns: XLSX_COLUMNS.length, inspection };
}

function parseExportArgs(args) {
  if (args.length < 2) {
    throw new ExportError(
      "usage: export_xlsx.mjs <results.json> <leads.xlsx> [--node-modules PATH] [--preview PATH] [--inspection PATH]",
    );
  }
  const options = {};
  for (let index = 2; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (!value) throw new ExportError(`${flag} requires a path`);
    if (flag === "--node-modules") options.nodeModules = value;
    else if (flag === "--preview") options.preview = value;
    else if (flag === "--inspection") options.inspection = value;
    else throw new ExportError(`unknown option: ${flag}`);
  }
  if (!options.nodeModules) {
    throw new ExportError("--node-modules is required for the Codex workbook runtime");
  }
  return { resultsPath: args[0], destination: args[1], options };
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const { resultsPath, destination, options } = parseExportArgs(args);
    const document = JSON.parse(await fs.readFile(resultsPath, "utf8"));
    const receipt = await exportXlsx(document, destination, options);
    process.stdout.write(`${JSON.stringify({ exported: true, path: destination, rows: receipt.rows, columns: receipt.columns })}\n`);
    return 0;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`${JSON.stringify({ exported: false, error: message })}\n`);
    return 2;
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) process.exitCode = await main();
