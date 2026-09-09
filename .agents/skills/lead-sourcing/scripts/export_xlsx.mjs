#!/usr/bin/env node

/** Export accepted TYCHE company-contact pairs to a styled Excel workbook. */

import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
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

export const CLIENT_XLSX_COLUMNS = [
  ...XLSX_COLUMNS.slice(0, 16), "Intent Signal", ...XLSX_COLUMNS.slice(16),
];
export const SOURCE_COLUMNS = [
  "Company", "Domain", "Field", "Signal", "Evidence Date", "Date Basis",
  "Observed On", "Source URL", "Evidence Text",
];
const TAXONOMY = JSON.parse(readFileSync(
  new URL("../assets/leadpoet_industry_taxonomy.json", import.meta.url), "utf8",
));

const COLUMN_LETTERS = [
  "A", "B", "C", "D", "E", "F", "G", "H", "I",
  "J", "K", "L", "M", "N", "O", "P", "Q", "R",
];

const COLUMN_WIDTHS = [
  24, 28, 38, 26, 44, 30, 40, 20, 28,
  18, 18, 18, 18, 18, 16, 48, 72, 20,
];

const ZEROBOUNCE_HARD_REJECTION_STATUSES = new Set([
  "invalid", "do_not_mail", "spamtrap", "abuse",
]);

export class ExportError extends Error {}

function isClientOutput(document) {
  const version = document.schema_version;
  if (version !== undefined && !["1.0", "1.1", "1.2"].includes(version)) {
    throw new ExportError("schema_version must be 1.0, 1.1 or 1.2");
  }
  return version === "1.2";
}

function validateClientRow(row, index) {
  if (typeof row.intent_details !== "string" || !row.intent_details.trim()) {
    throw new ExportError(`accepted[${index}].intent_details must be a non-empty string`);
  }
  const company = object(row.company);
  if ("classification_note" in company && (typeof company.classification_note !== "string" || !company.classification_note.trim())) {
    throw new ExportError(`accepted[${index}].company.classification_note must be a non-empty string`);
  }
  if (!("industry" in company) && !("sub_industry" in company)) {
    if (typeof company.classification_note !== "string" || !company.classification_note.trim()) {
      throw new ExportError(`accepted[${index}].company.classification_note is required for unresolved classification`);
    }
  } else if (
    typeof company.industry !== "string" || typeof company.sub_industry !== "string"
    || !TAXONOMY.parent_industries.includes(company.industry)
    || !Object.hasOwn(TAXONOMY.subindustry_parents, company.sub_industry)
    || !TAXONOMY.subindustry_parents[company.sub_industry].includes(company.industry)
  ) {
    throw new ExportError(`accepted[${index}].company requires an exact canonical industry/sub_industry pair`);
  }
}

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
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new ExportError(`${path} requires a valid email address`);
  }
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
  const normalizedStatus = status.toLowerCase();
  const fallbackEligible = validator === "zerobounce"
    && !["valid", ...ZEROBOUNCE_HARD_REJECTION_STATUSES].includes(normalizedStatus);
  if (validator === "bounceban") {
    if (status.toLowerCase() !== "success" || text(receipt.result).toLowerCase() !== "deliverable") {
      throw new ExportError(`${path} requires BounceBan success and result deliverable`);
    }
    if ("fallback" in receipt) throw new ExportError(`${path} cannot chain fallbacks`);
  } else if (fallbackEligible && Object.keys(object(fallback)).length) {
    validateEmailReceipt(document, { email_validation: fallback }, email, index, "bounceban", `${path}.fallback`);
    const ids = (document.routes || []).map((route) => text(object(route).route_id));
    if (ids.indexOf(text(object(fallback.source).route_id)) <= ids.indexOf(text(object(receipt.source).route_id))) {
      throw new ExportError(`${path} fallback must use a distinct later route`);
    }
  } else if (normalizedStatus !== "valid") {
    throw new ExportError(`${path}.status must be valid`);
  }
  if (validator === "zerobounce" && "fallback" in receipt && !fallbackEligible) {
    throw new ExportError(`${path} fallback is not allowed for valid or hard-rejection statuses`);
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
    || (!(["ok", "partial"].includes(route.provider_status)
      || (validator === "zerobounce"
        && fallbackEligible
        && ["no_results", "rate_limited", "auth_failed", "quota_exceeded",
          "timeout", "schema_error", "provider_error", "config_error"].includes(route.provider_status))))
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

  const clientOutput = isClientOutput(document);
  const requestedFields = requestedContactFields(document);
  return document.accepted.map((acceptedRow, index) => {
    if (!acceptedRow || typeof acceptedRow !== "object" || Array.isArray(acceptedRow)) {
      throw new ExportError(`accepted[${index}] must be an object`);
    }
    if (clientOutput) validateClientRow(acceptedRow, index);
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
    const storedEmail = text(contact.email);
    if (storedEmail) validateEmailReceipt(document, contact, storedEmail, index);
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
      ...(clientOutput ? { "Intent Signal": text(signal.signal) } : {}),
      "Intent Details": clientOutput ? text(acceptedRow.intent_details) : intentDetails(signal),
      Phone: phone,
    };
  });
}

function matrixFor(rows, columns = XLSX_COLUMNS, literalText = false) {
  return [
    columns,
    ...rows.map((row) => columns.map((column) => {
      const value = row[column];
      if (value === "") return null;
      return literalText && typeof value === "string" && value.startsWith("=") ? `'${value}` : value;
    })),
  ];
}

function calendarDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function sourcesFor(document) {
  const rows = [];
  for (const [index, row] of document.accepted.entries()) {
    const company = object(row.company);
    const add = (field, evidence, signal = "", evidencePath = field) => {
      const item = object(evidence);
      const url = item.evidence_url ?? item.url;
      const date = item.evidence_date ?? item.date;
      const basis = item.evidence_date_basis ?? item.date_basis;
      const excerpt = item.evidence_text ?? item.text;
      const source = object(item.source);
      if (
        typeof url !== "string" || !/^https?:\/\/[^\s]+$/.test(url)
        || !calendarDate(date)
        || !["published", "posted", "updated", "observed_current"].includes(basis)
        || typeof excerpt !== "string" || !excerpt.trim()
        || ["provider", "operation", "route_id"].some((key) => typeof source[key] !== "string" || !source[key].trim())
      ) throw new ExportError(`accepted[${index}].${evidencePath} requires dated source evidence`);
      const observed = basis === "observed_current" ? date : text(document.retrieved_at).slice(0, 10);
      if (!calendarDate(observed)) throw new ExportError("retrieved_at requires an observation date");
      rows.push({
        Company: text(company.canonical_name), Domain: text(company.domain), Field: field,
        Signal: signal, "Evidence Date": basis === "observed_current" ? "" : date,
        "Date Basis": basis, "Observed On": observed, "Source URL": url, "Evidence Text": excerpt,
      });
    };
    add("Description", row.account_fit, "", "account_fit");
    const signal = object(row.signal_evidence);
    if (typeof signal.signal !== "string" || !signal.signal.trim()) {
      throw new ExportError(`accepted[${index}].signal_evidence.signal is required`);
    }
    add("Intent Details", signal, signal.signal, "signal_evidence");
    add("Role", row.primary_contact, "", "primary_contact");
    for (const check of row.qualification_checks || []) {
      for (const evidence of check.evidence || []) add(check.criterion, evidence);
    }
    if (text(company.classification_note)) {
      rows.push({
        Company: text(company.canonical_name), Domain: text(company.domain), Field: "Industry",
        Signal: "", "Evidence Date": "", "Date Basis": "", "Observed On": "", "Source URL": "",
        "Evidence Text": company.classification_note,
      });
    }
  }
  return rows;
}

function wrappedRowHeight(values, widths) {
  const lines = Math.max(...values.map((value, index) => String(value ?? "").split("\n")
    .reduce((count, line) => count + Math.max(1, Math.ceil(line.length / (widths[index] * 0.85))), 0)));
  return Math.min(409, Math.max(36, lines * 15 + 12));
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
  const clientOutput = isClientOutput(document);
  const columns = clientOutput ? CLIENT_XLSX_COLUMNS : XLSX_COLUMNS;
  const sourceRows = clientOutput ? sourcesFor(document) : [];
  const lastColumn = clientOutput ? "S" : "R";
  const { Workbook, SpreadsheetFile } = await loadArtifactTool(options.nodeModules);
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("Leads");
  const lastRow = rows.length + 1;
  const usedRangeAddress = `A1:${lastColumn}${lastRow}`;

  sheet.getRange(usedRangeAddress).values = matrixFor(rows, columns, clientOutput);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(4);

  const header = sheet.getRange(`A1:${lastColumn}1`);
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
    const body = sheet.getRange(`A2:${lastColumn}${lastRow}`);
    body.format = {
      font: { color: "#1F2937", name: "Aptos", size: 10 },
      verticalAlignment: "top",
      rowHeight: 66,
    };
    sheet.getRange(`C2:C${lastRow}`).format.wrapText = true;
    sheet.getRange(`P2:${clientOutput ? "R" : "Q"}${lastRow}`).format.wrapText = true;
    sheet.getRange(`O2:O${lastRow}`).format.numberFormat = "#,##0";

    const table = sheet.tables.add(usedRangeAddress, true, "LeadsTable");
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }

  const widths = clientOutput ? [...COLUMN_WIDTHS.slice(0, 16), 30, ...COLUMN_WIDTHS.slice(16)] : COLUMN_WIDTHS;
  const letters = clientOutput ? [...COLUMN_LETTERS, "S"] : COLUMN_LETTERS;
  letters.forEach((column, index) => {
    sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = widths[index];
  });
  if (clientOutput) {
    if (rows.length) sheet.getRange(`A2:S${lastRow}`).format.wrapText = true;
    rows.forEach((row, index) => {
      sheet.getRange(`A${index + 2}:S${index + 2}`).format.rowHeight = wrappedRowHeight(
        columns.map((column) => row[column]), widths,
      );
    });
    const sources = workbook.worksheets.add("Sources");
    const sourceWidths = [26, 26, 24, 30, 16, 20, 16, 44, 88];
    const sourceLastRow = sourceRows.length + 1;
    const sourceMatrix = matrixFor(sourceRows, SOURCE_COLUMNS, true);
    for (const row of sourceMatrix.slice(1)) {
      for (const index of [4, 6]) if (row[index]) row[index] = new Date(`${row[index]}T00:00:00Z`);
    }
    sources.getRange(`A1:I${sourceLastRow}`).values = sourceMatrix;
    sources.showGridLines = false;
    sources.freezePanes.freezeRows(1);
    sources.getRange(`A1:I${sourceLastRow}`).format = {
      font: { name: "Aptos", size: 10, color: "#1F2937" },
      verticalAlignment: "top", wrapText: true,
    };
    sources.getRange("A1:I1").format = {
      fill: "#0F766E", font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" },
      rowHeight: 30, wrapText: true,
    };
    SOURCE_COLUMNS.forEach((column, index) => {
      sources.getRangeByIndexes(0, index, sourceLastRow, 1).format.columnWidth = sourceWidths[index];
    });
    if (sourceRows.length) {
      for (const column of ["E", "G"]) sources.getRange(`${column}2:${column}${sourceLastRow}`).setNumberFormat("yyyy-mm-dd");
      sourceRows.forEach((row, index) => {
        sources.getRange(`A${index + 2}:I${index + 2}`).format.rowHeight = wrappedRowHeight(
          SOURCE_COLUMNS.map((column) => row[column]), sourceWidths,
        );
      });
      const sourceTable = sources.tables.add(`A1:I${sourceLastRow}`, true, "SourcesTable");
      sourceTable.style = "TableStyleMedium2";
      sourceTable.showFilterButton = true;
    }
  }

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

  return { rows: rows.length, columns: columns.length, inspection };
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
