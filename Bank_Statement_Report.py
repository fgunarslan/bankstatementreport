import io
import re
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import pandas as pd
import streamlit as st

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


st.set_page_config(page_title="Bank Statement Report", layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stDataFrame"] table {
        table-layout: fixed !important;
        width: 100% !important;
    }

    /* Hide index column so widths below line up with real data columns */
    div[data-testid="stDataFrame"] thead tr th:first-child,
    div[data-testid="stDataFrame"] tbody tr td:first-child {
        display: none !important;
    }

    /* Transfer Date */
    div[data-testid="stDataFrame"] thead tr th:nth-child(2),
    div[data-testid="stDataFrame"] tbody tr td:nth-child(2) {
        width: 110px !important;
        min-width: 110px !important;
        max-width: 110px !important;
    }

    /* Description */
    div[data-testid="stDataFrame"] thead tr th:nth-child(3),
    div[data-testid="stDataFrame"] tbody tr td:nth-child(3) {
        width: auto !important;
    }

    /* Amount */
    div[data-testid="stDataFrame"] thead tr th:nth-child(4),
    div[data-testid="stDataFrame"] tbody tr td:nth-child(4) {
        width: 115px !important;
        min-width: 115px !important;
        max-width: 115px !important;
        text-align: right !important;
    }

    /* Source File */
    div[data-testid="stDataFrame"] thead tr th:nth-child(5),
    div[data-testid="stDataFrame"] tbody tr td:nth-child(5) {
        width: 170px !important;
        min-width: 170px !important;
        max-width: 170px !important;
    }

    .report-table th.col-amount,
    .report-table td.col-amount {
        text-align: right !important;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULT_USD_THRESHOLD = 20000.0

DATE_RE = re.compile(r"^\d{2}/\d{2}$")
AMOUNT_RE = re.compile(r"^-?\$?\d[\d,]*\.\d{2}$")

BANK_NAME_PATTERNS = [
    r"JPMORGAN\s+CHASE",
    r"CHASE",
    r"MORGAN\s+STANLEY",
    r"BANK\s+OF\s+AMERICA",
    r"CITI(?:BANK)?",
    r"WELLS\s+FARGO",
    r"TD\s+BANK",
    r"CHARLES\s+SCHWAB",
    r"INTERACTIVE\s+BROKERS",
    r"GOLDMAN\s+SACHS",
]

ACCOUNT_NAME_MAP = {
    "3248": "Ahmet Okumus",
    "0244": "RPD Fund Management LLC.",
    "4632": "Okumus Opportunistic Value Fund",
    "5556": "Babacan Scuttlehole",
}


@dataclass
class ParsedRecord:
    bank_account_label: str
    account_number_last4: str
    account_name: str
    statement_period: str
    transfer_date: str
    description: str
    amount_usd: float
    source_file: str


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_pdf_text(file_bytes: bytes) -> str:
    texts: List[str] = []

    if pdfplumber is not None:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    texts.append(page.extract_text() or "")
            combined = "\n".join(texts).strip()
            if combined:
                return combined
        except Exception:
            pass

    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages:
                texts.append(page.extract_text() or "")
            return "\n".join(texts)
        except Exception:
            pass

    return ""


def extract_first_page_header_text(file_bytes: bytes) -> str:
    if pdfplumber is None:
        return ""

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                return ""

            page = pdf.pages[0]
            header_height = min(260, page.height)
            cropped = page.crop((0, 0, page.width, header_height))
            header_text = cropped.extract_text() or ""

            words = page.extract_words(use_text_flow=True) or []
            top_words = [w for w in words if float(w.get("top", 9999)) <= 260]
            if top_words:
                top_words = sorted(top_words, key=lambda w: (round(float(w["top"]), 0), float(w["x0"])))
                header_text = f"{header_text} {' '.join(w['text'] for w in top_words)}"

            return normalize_space(header_text)
    except Exception:
        return ""


def extract_statement_period_from_pdf(file_bytes: bytes, fallback_text: str) -> str:
    header_text = extract_first_page_header_text(file_bytes)

    patterns = [
        re.compile(
            r'([A-Za-z]+\s+\d{1,2},\s+\d{4}\s+through\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})',
            re.IGNORECASE,
        ),
        re.compile(
            r'(For\s+the\s+Period\s+\d{1,2}/\d{1,2}/\d{2,4}\s+to\s+\d{1,2}/\d{1,2}/\d{2,4})',
            re.IGNORECASE,
        ),
    ]

    for source in [header_text, normalize_space(fallback_text[:15000])]:
        for pattern in patterns:
            m = pattern.search(source)
            if m:
                return m.group(1).strip()

    return "Not detected"


def detect_bank_name(text: str, fallback_name: str) -> str:
    head = text[:5000].upper()
    for pattern in BANK_NAME_PATTERNS:
        m = re.search(pattern, head, re.IGNORECASE)
        if m:
            return m.group(0).title()
    return fallback_name


def detect_account_identifier(text: str, file_bytes: Optional[bytes] = None) -> str:
    candidates: List[str] = []

    if file_bytes is not None:
        header_text = extract_first_page_header_text(file_bytes)
        if header_text:
            candidates.append(header_text)

    candidates.append(normalize_space(text[:12000]))

    patterns = [
        r"Primary\s+Account[:\s]*0*([0-9]{4})\b",
        r"Primary\s+Account[:\s]*([0-9]{5,})",
        r"Account\s+Number[:\s]*0*([0-9]{4})\b",
        r"Account\s+Number[:\s]*([0-9]{5,})",
        r"ending\s+in[:\s]*([0-9]{4})\b",
    ]

    for source in candidates:
        for pattern in patterns:
            m = re.search(pattern, source, re.IGNORECASE)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if digits:
                    return digits[-4:]

    return "Unknown"


def map_account_name(last4: str) -> str:
    return ACCOUNT_NAME_MAP.get(last4, "Unknown Account")


def parse_amount(amount_text: str) -> float:
    cleaned = amount_text.replace("$", "").replace(",", "").strip()
    return float(cleaned)


def format_amount(amount: float) -> str:
    return f"{amount:,.2f}"


def format_full_date(mmdd: str, year: int) -> str:
    month, day = mmdd.split("/")
    return f"{month}/{day}/{year}"


def is_section_heading(text: str) -> bool:
    t = normalize_space(text).lower()
    headings = [
        "payments & transfers",
        "deposits & credits",
        "atm & debit card withdrawals",
        "checks paid",
        "account messages",
        "daily ending balance",
        "electronic withdrawals",
        "electronic deposits",
    ]
    return any(h in t for h in headings)


def group_words_into_lines(words: List[dict], tolerance: float = 3.0) -> List[List[dict]]:
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines: List[List[dict]] = []
    current: List[dict] = [words_sorted[0]]
    current_top = float(words_sorted[0]["top"])

    for w in words_sorted[1:]:
        top = float(w["top"])
        if abs(top - current_top) <= tolerance:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda z: float(z["x0"])))
            current = [w]
            current_top = top
    lines.append(sorted(current, key=lambda z: float(z["x0"])))
    return lines


def parse_payments_transfers_rows_from_pdf(file_bytes: bytes) -> List[dict]:
    if pdfplumber is None:
        return []

    parsed_rows: List[dict] = []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
                if not words:
                    continue

                lines = group_words_into_lines(words, tolerance=3.2)

                in_payments = False
                current_row = None

                for line in lines:
                    texts = [w["text"] for w in line]
                    line_text = normalize_space(" ".join(texts)).replace(" :", ":")
                    lower_text = line_text.lower()

                    if "payments & transfers" in lower_text:
                        in_payments = True
                        current_row = None
                        continue

                    if in_payments and is_section_heading(line_text) and "payments & transfers" not in lower_text:
                        if current_row:
                            parsed_rows.append(current_row)
                            current_row = None
                        in_payments = False
                        continue

                    if not in_payments:
                        continue

                    if lower_text in {"date description amount", "date description balance amount", "date description"}:
                        continue
                    if lower_text.startswith("date ") or lower_text == "amount" or lower_text == "description":
                        continue

                    date_token = texts[0] if texts else ""
                    amount_token = texts[-1] if texts else ""

                    has_date = bool(DATE_RE.match(date_token))
                    has_amount = bool(AMOUNT_RE.match(amount_token))

                    if has_date and has_amount:
                        if current_row:
                            parsed_rows.append(current_row)

                        description_tokens = texts[1:-1]
                        description = normalize_space(" ".join(description_tokens))
                        if description.startswith(date_token + " "):
                            description = description[len(date_token):].strip()

                        current_row = {
                            "date": date_token,
                            "description": description,
                            "amount": amount_token,
                        }
                        continue

                    if current_row:
                        if not has_date and not is_section_heading(line_text):
                            extra = line_text
                            if AMOUNT_RE.match(texts[-1]) and len(texts) <= 2:
                                extra = ""
                            if extra:
                                current_row["description"] = normalize_space(
                                    current_row["description"] + " " + extra
                                )

                if current_row:
                    parsed_rows.append(current_row)

    except Exception:
        return []

    return parsed_rows


def parse_statement(
    file_name: str,
    file_bytes: bytes,
    usd_threshold: float,
    statement_year: int,
) -> Tuple[List[ParsedRecord], Dict[str, str], str]:
    ext = file_name.lower().split(".")[-1]
    text = ""

    if ext == "pdf":
        text = read_pdf_text(file_bytes)
    elif ext in {"txt", "csv"}:
        text = file_bytes.decode("utf-8", errors="ignore")
    else:
        text = file_bytes.decode("utf-8", errors="ignore")

    if not text.strip():
        return [], {}, "Could not extract text from the file. OCR may be required if the PDF is image-based."

    bank_name = detect_bank_name(text, fallback_name=file_name)
    account_last4 = detect_account_identifier(text, file_bytes if ext == "pdf" else None)
    account_name = map_account_name(account_last4)
    statement_period = extract_statement_period_from_pdf(file_bytes, text) if ext == "pdf" else "Not detected"
    label = f"{bank_name} | {account_last4} | {account_name}"

    records: List[ParsedRecord] = []

    if ext == "pdf":
        table_rows = parse_payments_transfers_rows_from_pdf(file_bytes)
        for row in table_rows:
            try:
                amount = parse_amount(row["amount"])
            except Exception:
                continue

            if abs(amount) < usd_threshold:
                continue

            records.append(
                ParsedRecord(
                    bank_account_label=label,
                    account_number_last4=account_last4,
                    account_name=account_name,
                    statement_period=statement_period,
                    transfer_date=format_full_date(row["date"], statement_year),
                    description=normalize_space(row["description"]),
                    amount_usd=amount,
                    source_file=file_name,
                )
            )
    else:
        lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
        row_pattern = re.compile(
            r'^\s*(?P<date>\d{2}/\d{2})\s+(?P<description>.+?)\s+(?P<amount>-?\$?\d[\d,]*\.\d{2})\s*$'
        )
        for line in lines:
            m = row_pattern.match(line)
            if not m:
                continue
            try:
                amount = parse_amount(m.group("amount"))
            except Exception:
                continue
            if abs(amount) < usd_threshold:
                continue
            records.append(
                ParsedRecord(
                    bank_account_label=label,
                    account_number_last4=account_last4,
                    account_name=account_name,
                    statement_period=statement_period,
                    transfer_date=format_full_date(m.group("date"), statement_year),
                    description=normalize_space(m.group("description")),
                    amount_usd=amount,
                    source_file=file_name,
                )
            )

    meta = {
        "bank_account_label": label,
        "account_number_last4": account_last4,
        "account_name": account_name,
        "statement_period": statement_period,
        "source_file": file_name,
    }

    warning = ""
    if ext == "pdf" and not records:
        warning = "No matching 20k+ transactions were found in the Payments & Transfers section."

    return records, meta, warning


def records_to_dataframe(records: List[ParsedRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        rows.append(
            {
                "Bank Account": r.bank_account_label,
                "Account Number": r.account_number_last4,
                "Account Name": r.account_name,
                "Statement Period": r.statement_period,
                "Transfer Date": r.transfer_date,
                "Description": r.description,
                "Amount": format_amount(r.amount_usd),
                "Source File": r.source_file,
            }
        )
    return pd.DataFrame(rows)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Transfers")
    output.seek(0)
    return output.read()


st.title("Bank Statement Report")
st.caption(
    "Reads only the Payments & Transfers section and lists transactions of 20,000 USD and above. "
    "Deposits & Credits and Balance are ignored."
)

with st.sidebar:
    st.header("Settings")
    threshold = st.number_input(
        "Minimum amount (USD)",
        min_value=0.0,
        value=DEFAULT_USD_THRESHOLD,
        step=1000.0,
    )
    selected_year = st.number_input(
        "Statement year",
        min_value=2000,
        max_value=2100,
        value=2026,
        step=1,
    )
    st.info("The check is based only on the Amount column in the Payments & Transfers section.")

uploaded_files = st.file_uploader(
    "Upload statement files (PDF/TXT/CSV)",
    type=["pdf", "txt", "csv"],
    accept_multiple_files=True,
)

if uploaded_files:
    all_records: List[ParsedRecord] = []
    warnings: List[str] = []

    for uploaded in uploaded_files:
        file_bytes = uploaded.read()
        records, _meta, warning = parse_statement(
            uploaded.name,
            file_bytes,
            float(threshold),
            int(selected_year),
        )
        all_records.extend(records)
        if warning:
            warnings.append(f"{uploaded.name}: {warning}")

    if warnings:
        st.subheader("Warnings")
        for w in warnings:
            st.warning(w)

    if all_records:
        df = records_to_dataframe(all_records)
        df = df.sort_values(by=["Bank Account", "Transfer Date"], ascending=[True, True])

        st.subheader("Account-Based Report")
        for account_name_label, group in df.groupby("Bank Account"):
            st.markdown(f"### {account_name_label}")
            account_number = group["Account Number"].iloc[0] if not group.empty else "-"
            account_name = group["Account Name"].iloc[0] if not group.empty else "-"
            statement_period = group["Statement Period"].iloc[0] if not group.empty else "-"
            st.write(f"**Account Number:** {account_number}")
            st.write(f"**Account Name:** {account_name}")
            st.write(f"**Statement Period:** {statement_period}")
            st.write(f"**Transaction count:** {len(group)}")
            total_amount = group["Amount"].astype(str).str.replace(",", "", regex=False).astype(float).sum()
            st.write(f"**Total Amount:** ${total_amount:,.2f}")
            display_group = group[["Transfer Date", "Description", "Amount", "Source File"]]
            st.dataframe(
                display_group,
                use_container_width=True,
                column_config={
                    "Transfer Date": st.column_config.TextColumn("Transfer Date", width="small"),
                    "Description": st.column_config.TextColumn("Description", width="large"),
                    "Amount": st.column_config.TextColumn("Amount", width="small"),
                    "Source File": st.column_config.TextColumn("Source File", width="small"),
                },
                column_order=["Transfer Date", "Description", "Amount", "Source File"],
                hide_index=True,
            )

        excel_bytes = to_excel_bytes(df)
        st.download_button(
            "Download Excel report",
            data=excel_bytes,
            file_name="bank_statement_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Download CSV",
            data=csv_bytes,
            file_name="bank_statement_report.csv",
            mime="text/csv",
        )
    else:
        st.info("No transactions matching the threshold were found in the Payments & Transfers section.")
else:
    st.markdown(
        """
### Current rules
- Only the **Payments & Transfers** section is read
- The **Deposits & Credits** section is completely ignored
- For PDFs, rows are read from table coordinates
- The last 4 digits are read from `Primary Account:` or `Account Number:`
- The account name is assigned automatically based on the last 4 digits
- `February ... through ...` or `For the Period ... to ...` is captured exactly as written
- The check is based only on the Amount column
- Balance is completely ignored
        """
    )
