#!/usr/bin/env node

/** Export accepted TYCHE company-contact pairs to a styled Excel workbook. */

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

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
  "Contact City",
  "Contact State",
  "Contact Country",
  "HQ State",
  "HQ Country",
  "Company Employee Range",
  "Description",
  "Intent Details",
  "Phone",
];

export const CLIENT_XLSX_COLUMNS = [
  ...XLSX_COLUMNS.slice(0, 16), "Signals", ...XLSX_COLUMNS.slice(16),
];
export const SOURCE_COLUMNS = [
  "Company", "Domain", "Field", "Signal", "Evidence Date", "Date Basis",
  "Observed On", "Source URL", "Evidence Text",
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

function isClientOutput(document) {
  const version = document.schema_version;
  if (version !== undefined && !["1.0", "1.1", "1.2"].includes(version)) {
    throw new ExportError("schema_version must be 1.0, 1.1 or 1.2");
  }
  return version === "1.2";
}

function validateOutput(document, resultsPath) {
  const checked = spawnSync(process.env.TYCHE_WORKSPACE_PYTHON || "python3", [
    fileURLToPath(new URL("./validate_run.py", import.meta.url)), resultsPath || "-", "--check-output",
  ], { input: resultsPath ? undefined : JSON.stringify(document), encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024 });
  if (checked.error || checked.status !== 0) {
    throw new ExportError(`Output validation failed: ${checked.error?.message || checked.stdout || checked.stderr}`);
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

function employeeRange(value, field) {
  const normalized = text(value).replace(/[\s,]/g, "").replace(/[–—]/g, "-");
  const match = /^(\d+)(?:-(\d+)|(\+))$/.exec(normalized);
  if (!match || (match[2] !== undefined && Number(match[2]) < Number(match[1]))) {
    throw new ExportError(`${field} requires the LinkedIn employee range`);
  }
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

function signalsFor(row) {
  const signals = [object(row.signal_evidence)];
  for (const check of row.qualification_checks || []) {
    if (check.status !== "pass" || !text(check.signal)) continue;
    for (const evidence of check.evidence || []) {
      signals.push({ signal: check.signal, evidence_date: evidence.date,
        evidence_date_basis: evidence.date_basis, evidence_text: evidence.text,
        evidence_url: evidence.url });
    }
  }
  return [...new Set(signals.map((signal) => {
    const dateLabel = signal.evidence_date_basis === "observed_current" ? "Observed on" : "Source date";
    return [text(signal.signal),
      text(signal.evidence_date) ? `${dateLabel}: ${text(signal.evidence_date)}` : "",
      text(signal.evidence_text),
      text(signal.evidence_url) ? `Source: ${text(signal.evidence_url)}` : "",
    ].filter(Boolean).join("\n");
  }))].join("\n\n");
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

export function rowsFor(document, resultsPath) {
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new ExportError("results.json must contain one JSON object");
  }
  if (!Array.isArray(document.accepted)) {
    throw new ExportError("results.json accepted must be an array");
  }

  const clientOutput = isClientOutput(document);
  validateOutput(document, resultsPath);
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

    const range = employeeRange(company.employee_range, `accepted[${index}].company.employee_range`);
    const email = requestedValue(contact, "email", requestedFields, index);
    const phone = requestedValue(contact, "phone", requestedFields, index);
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
      "Contact City": text(contact.city),
      "Contact State": text(contact.state),
      "Contact Country": text(contact.country),
      "HQ State": text(company.hq_state),
      "HQ Country": text(company.hq_country),
      "Company Employee Range": range,
      Description: text(company.description),
      ...(clientOutput ? { Signals: signalsFor(acceptedRow) } : {}),
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

export function sourcesFor(document) {
  validateOutput(document);
  const rows = [];
  for (const row of document.accepted) {
    const company = object(row.company);
    const add = (field, evidence, signal = "") => {
      const item = object(evidence);
      const url = item.evidence_url ?? item.url;
      const date = item.evidence_date ?? item.date;
      const basis = item.evidence_date_basis ?? item.date_basis;
      const excerpt = item.evidence_text ?? item.text;
      const observed = basis === "observed_current" ? date : text(document.retrieved_at).slice(0, 10);
      rows.push({
        Company: text(company.canonical_name), Domain: text(company.domain), Field: field,
        Signal: signal, "Evidence Date": basis === "observed_current" ? "" : date,
        "Date Basis": basis, "Observed On": observed, "Source URL": url, "Evidence Text": excerpt,
      });
    };
    add("Description", row.account_fit);
    const signal = object(row.signal_evidence);
    add("Signals", signal, signal.signal);
    add("Role", row.primary_contact);
    add("Contact Location", row.primary_contact.location_evidence);
    add("Company Employee Range", company.employee_range_evidence);
    for (const check of row.qualification_checks || []) {
      for (const evidence of check.evidence || []) {
        add(check.status === "pass" && text(check.signal) ? "Signals" : check.criterion,
          evidence, text(check.signal));
      }
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
  if (!options.resultsPath) throw new ExportError("resultsPath is required to verify saved provider receipts");
  const saved = JSON.parse(await fs.readFile(options.resultsPath, "utf8"));
  if (JSON.stringify(saved) !== JSON.stringify(document)) throw new ExportError("export document differs from saved results");
  const rows = rowsFor(document, options.resultsPath);
  const clientOutput = isClientOutput(document);
  const columns = clientOutput ? CLIENT_XLSX_COLUMNS : XLSX_COLUMNS;
  const sourceRows = clientOutput ? sourcesFor(document) : [];
  const lastColumn = clientOutput ? "S" : "R";
  const { Workbook, SpreadsheetFile, FileBlob } = await loadArtifactTool(options.nodeModules);
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

  const widths = clientOutput ? [...COLUMN_WIDTHS.slice(0, 16), 72, ...COLUMN_WIDTHS.slice(16)] : COLUMN_WIDTHS;
  const letters = clientOutput ? [...COLUMN_LETTERS, "S"] : COLUMN_LETTERS;
  letters.forEach((column, index) => {
    sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = widths[index];
  });
  if (clientOutput) {
    if (rows.length) sheet.getRange(`A2:${lastColumn}${lastRow}`).format.wrapText = true;
    rows.forEach((row, index) => {
      sheet.getRange(`A${index + 2}:${lastColumn}${index + 2}`).format.rowHeight = wrappedRowHeight(
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

  workbook.recalculate();
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
  const temporaryDirectory = await fs.mkdtemp(path.join(path.dirname(path.resolve(destination)), ".tyche-xlsx-"));
  try {
    const temporaryWorkbook = path.join(temporaryDirectory, "leads.xlsx");
    await output.save(temporaryWorkbook);
    const restored = await SpreadsheetFile.importXlsx(await FileBlob.load(temporaryWorkbook));
    const actual = restored.worksheets.getItem("Leads").getRange(usedRangeAddress).values;
    const expected = matrixFor(rows, columns);
    const formulas = restored.worksheets.getItem("Leads").getRange(usedRangeAddress).formulas;
    if (formulas.flat().some(value => typeof value === "string" && value.startsWith("="))) throw new ExportError("Saved lead cells must be literal values");
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new ExportError("Saved workbook values differ from validated lead rows");
    }
    if (clientOutput) {
      const sourceValues = restored.worksheets.getItem("Sources").getRange(`A1:I${sourceRows.length + 1}`).values;
      const expectedSources = matrixFor(sourceRows, SOURCE_COLUMNS);
      const sourceFormulas = restored.worksheets.getItem("Sources").getRange(`A1:I${sourceRows.length + 1}`).formulas;
      if (sourceFormulas.flat().some(value => typeof value === "string" && value.startsWith("="))) throw new ExportError("Saved source cells must be literal values");
      for (let i = 0; i < expectedSources.length; i++) {
        for (let j = 0; j < SOURCE_COLUMNS.length; j++) {
          const expectedValue = expectedSources[i][j];
          const actualValue = sourceValues[i][j];
          // Excel stores these calendar-date cells as serial numbers.
          const dateValue = i > 0 && [4, 6].includes(j) && expectedValue
            ? (Date.parse(`${expectedValue}T00:00:00Z`) - Date.UTC(1899, 11, 30)) / 86400000 : expectedValue;
          if (actualValue !== dateValue) throw new ExportError("Saved Sources values differ from validated evidence");
        }
      }
    }
    if (JSON.stringify(JSON.parse(await fs.readFile(options.resultsPath, "utf8"))) !== JSON.stringify(document)) {
      throw new ExportError("Saved results changed during export; review and finalize again");
    }
    await fs.rename(temporaryWorkbook, destination);
  } finally {
    await fs.rm(temporaryDirectory, { recursive: true, force: true });
  }

  const inspection = {
    used_range: usedRangeAddress,
    saved_workbook_values_verified: true,
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
  if (!args.length) throw new ExportError("usage: export_xlsx.mjs <results.json> [leads.xlsx] [--node-modules PATH] [--preview PATH] [--inspection PATH]");
  const resultsPath = path.resolve(args[0]);
  const explicitDestination = args[1] && !args[1].startsWith("--");
  const destination = explicitDestination ? args[1] : path.join(path.dirname(resultsPath), "leads.xlsx");
  const options = {
    nodeModules: process.env.TYCHE_WORKSPACE_NODE_MODULES,
    preview: path.join(path.dirname(destination), "leads-preview.png"),
    inspection: path.join(path.dirname(destination), "leads-inspection.json"),
  };
  for (let index = explicitDestination ? 2 : 1; index < args.length; index += 2) {
    const flag = args[index], value = args[index + 1];
    if (!value) throw new ExportError(`${flag} requires a path`);
    if (flag === "--node-modules") options.nodeModules = value;
    else if (flag === "--preview") options.preview = value;
    else if (flag === "--inspection") options.inspection = value;
    else throw new ExportError(`unknown option: ${flag}`);
  }
  if (!options.nodeModules) throw new ExportError("--node-modules is required unless TYCHE_WORKSPACE_NODE_MODULES is configured");
  return { resultsPath, destination, options };
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const { resultsPath, destination, options } = parseExportArgs(args);
    const validation = spawnSync(process.env.TYCHE_WORKSPACE_PYTHON || "python3", [
      fileURLToPath(new URL("./run_attempt.py", import.meta.url)), resultsPath, "--finalize",
    ], { encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024 });
    const checked = validation.status === 0 ? JSON.parse(validation.stdout) : null;
    if (!checked?.delivery_allowed) throw new ExportError(`Strict delivery validation failed: ${validation.error?.message || validation.stdout || validation.stderr}`);
    const resultText = await fs.readFile(resultsPath, "utf8");
    if (createHash("sha256").update(resultText).digest("hex") !== checked.results_sha256) throw new ExportError("Saved results changed during validation");
    const document = JSON.parse(resultText);
    const receipt = await exportXlsx(document, destination, { ...options, resultsPath });
    await fs.writeFile(path.join(path.dirname(destination), "validation.json"), JSON.stringify({ ...checked, completed_at: new Date().toISOString() }, null, 2) + "\n");
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
