from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
import re
import json

app = FastAPI(
    title="MM Purchasing Remediator (SAP Note 1803189)"
)

# -----------------------------
# Reference mappings (1803189)
# -----------------------------

# Classic -> Enjoy (Transactions)
TXN_MAP: Dict[str, List[str]] = {
    # PO transactions
    "ME21": ["ME21N"],
    "ME22": ["ME22N"],
    "ME23": ["ME23N"],
    "ME24": ["ME21N", "ME22N"],        # release/check -> use ME21N/ME22N depending on need
    "ME25": ["ME21N"],                 # create with acct asst -> use Hold in ME21N
    "ME27": ["ME21N"],                 # PO w/o material -> ME21N
    "ME28": ["ME29N"],                 # PO release -> ME29N
    # PR transactions
    "ME51": ["ME51N"],
    "ME52": ["ME52N"],
    "ME53": ["ME53N"],
    "ME54": ["ME54N"],
    "ME59": ["ME59N"],
}

# BAPI & FM replacements
BAPI_MAP: Dict[str, Dict[str, Any]] = {
    # PO BAPIs
    "BAPI_PO_CREATE": {"new": "BAPI_PO_CREATE1", "note": "Use Enjoy PO BAPI."},
    "BAPI_PO_GETDETAIL": {"new": "BAPI_PO_GETDETAIL1", "note": "Use Enjoy PO BAPI."},
    # PR BAPIs
    "BAPI_REQUISITION_CREATE": {"new": "BAPI_PR_CREATE", "note": "Use PR Enjoy BAPI."},
    "BAPI_REQUISITION_CHANGE": {"new": "BAPI_PR_CHANGE", "note": "Use PR Enjoy BAPI."},
    "BAPI_REQUISITION_DELETE": {"new": "BAPI_PR_CHANGE", "note": "Delete via CHANGE in Enjoy BAPI."},
    "BAPI_REQUISITION_GETDETAIL": {"new": "BAPI_PR_GETDETAIL", "note": "Use PR Enjoy BAPI."},
}

# IDoc input function modules
IDOC_MAP: Dict[str, Dict[str, Any]] = {
    "IDOC_INPUT_PORDCR": {"new": "IDOC_INPUT_PORDCR1", "note": "Use new PO IDoc FM (BUS2012)."},
    # PORDCH exists as the change FM; ensure BUS2012 version is used (name may be the same)
    "IDOC_INPUT_PORDCH": {"new": "IDOC_INPUT_PORDCH", "note": "Ensure BUS2012 version is used."},
    "IDOC_INPUT_PREQCR": {"new": "IDOC_INPUT_PREQCR1", "note": "Use new PR IDoc FM (BUS2105)."},
}

# Archiving reports (ECC 6.0 EhP4+ -> *70; older *47/*30 obsolete)
ARCHIVE_REPORT_MAP: Dict[str, Dict[str, Any]] = {
    # MM_EKKO
    "RM06EV47": {"new": "RM06EV70", "obj": "MM_EKKO"},
    "RM06EW47": {"new": "RM06EW70", "obj": "MM_EKKO"},
    "RM06ED47": {"new": "RM06ED70", "obj": "MM_EKKO"},
    # MM_EBAN
    "RM06BV47": {"new": "RM06BV70", "obj": "MM_EBAN"},
    "RM06BW47": {"new": "RM06BW70", "obj": "MM_EBAN"},
    "RM06BD47": {"new": "RM06BD70", "obj": "MM_EBAN"},
    # MM_EINA
    "RM06IW47": {"new": "RM06IW70", "obj": "MM_EINA"},
    "RM06ID47": {"new": "RM06ID70", "obj": "MM_EINA"},
}

# Very old archiving (*30) -> generic advice to move to *70
ARCHIVE_30_PREFIXES = ["RM06E", "RM06B", "RM06I"]  # EKKO/EBAN/EINA families
# Read programs: still usable, but recommend SARI
READ_REPORTS = {"RM06ER30", "RM06BR30", "RM06IR30"}

# -----------------------------
# Regexes
# -----------------------------

# CALL TRANSACTION / SUBMIT for transactions
TXN_NAMES = sorted(TXN_MAP.keys(), key=len, reverse=True)
TXN_RE = re.compile(
    rf"""
    (?P<full>
        (?P<stmt>CALL\s+TRANSACTION|SUBMIT)
        \s+
        ['"]?(?P<name>{'|'.join(map(re.escape, TXN_NAMES))})['"]?
        \s*\.?
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# CALL FUNCTION for BAPIs/IDoc FMs
FUNC_NAMES = sorted(set(BAPI_MAP.keys()) | set(IDOC_MAP.keys()), key=len, reverse=True)
if FUNC_NAMES:
    FUNC_RE = re.compile(
        rf"""
        (?P<full>
            CALL\s+FUNCTION
            \s+
            ['"](?P<name>{'|'.join(map(re.escape, FUNC_NAMES))})['"]
            [^\.]*\.
        )
        """,
        re.IGNORECASE | re.VERBOSE | re.DOTALL
    )
else:
    FUNC_RE = None

# SUBMIT for archiving reports (*47 -> *70)
ARCH47_NAMES = sorted(ARCHIVE_REPORT_MAP.keys(), key=len, reverse=True)
ARCH47_RE = re.compile(
    rf"""
    (?P<full>
        (?P<stmt>SUBMIT)
        \s+
        (?P<name>{'|'.join(map(re.escape, ARCH47_NAMES))})
        \b
        [^\.]*\.
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL
)

# SUBMIT for *30 family (very old)
ARCH30_RE = re.compile(
    rf"""
    (?P<full>
        (?P<stmt>SUBMIT)
        \s+
        (?P<name>(?:{'|'.join(map(re.escape, ARCHIVE_30_PREFIXES))})[A-Z]*30)
        \b
        [^\.]*\.
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL
)

# SUBMIT read-programs (still usable; recommend SARI)
READ_RE = re.compile(
    rf"""
    (?P<full>
        (?P<stmt>SUBMIT)
        \s+
        (?P<name>{'|'.join(map(re.escape, READ_REPORTS))})
        \b
        [^\.]*\.
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL
)

# -----------------------------
# Models
# -----------------------------

class Unit(BaseModel):
    pgm_name: str
    inc_name: str
    type: str
    name: Optional[str] = None
    class_implementation: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    code: Optional[str] = ""

# -----------------------------
# Helpers
# -----------------------------

def _mk_suggested_statement(kind: str, old: str, news: List[str], stmt: str) -> str:
    """
    kind: 'txn' | 'func' | 'report'
    stmt: the ABAP leading keyword matched (CALL TRANSACTION / SUBMIT / CALL FUNCTION)
    """
    up = stmt.upper()
    if kind == "txn":
        # If multiple options (e.g. ME24 -> ME21N/ME22N), put both for human review
        if len(news) == 1:
            if up.startswith("SUBMIT"):
                return f"SUBMIT {news[0]}."
            else:
                return f"CALL TRANSACTION '{news[0]}'."
        else:
            # Provide both choices, caller can decide
            if up.startswith("SUBMIT"):
                return " or ".join([f"SUBMIT {n}." for n in news])
            else:
                return " or ".join([f"CALL TRANSACTION '{n}'." for n in news])

    if kind == "func":
        # Always CALL FUNCTION
        return f"CALL FUNCTION '{news[0]}'."

    if kind == "report":
        # Always SUBMIT
        return f"SUBMIT {news[0]}."
    return ""


def _add_hit(
    hits: List[dict],
    span: Tuple[int, int],
    target_type: str,
    target_name: str,
    suggested_statement: str,
    note: Optional[str] = None,
    ambiguous: bool = False
):
    meta = {
        "table": "None",
        "target_type": target_type,
        "target_name": target_name,
        "start_char_in_unit": span[0],
        "end_char_in_unit": span[1],
        "used_fields": [],
        "ambiguous": ambiguous,
        "suggested_statement": suggested_statement,
        "suggested_fields": None
    }
    if note:
        meta["note"] = note
    hits.append(meta)


def find_mm_purchasing_issues(txt: str) -> List[dict]:
    if not txt:
        return []

    issues: List[dict] = []

    # 1) Transactions
    for m in TXN_RE.finditer(txt):
        stmt = m.group("stmt")
        name = m.group("name").upper()
        repls = TXN_MAP.get(name, [])
        if repls:
            ambiguous = len(repls) > 1
            suggested = _mk_suggested_statement("txn", name, repls, stmt)
            note = None
            if name == "ME25":
                note = "ME25 functions available via ME21N (use Hold / Enjoy features)."
            if name == "ME27":
                note = "PO without material: create via ME21N."
            _add_hit(
                issues, m.span("full"), "Transaction", name, suggested, note=note, ambiguous=ambiguous
            )

    # 2) BAPIs & IDoc input FMs
    if FUNC_RE:
        for m in FUNC_RE.finditer(txt):
            name = m.group("name").upper()
            stmt = "CALL FUNCTION"
            if name in BAPI_MAP:
                new = BAPI_MAP[name]["new"]
                note = BAPI_MAP[name].get("note")
                suggested = _mk_suggested_statement("func", name, [new], stmt)
                _add_hit(issues, m.span("full"), "FunctionModule", name, suggested, note=note)
            elif name in IDOC_MAP:
                new = IDOC_MAP[name]["new"]
                note = IDOC_MAP[name].get("note")
                suggested = _mk_suggested_statement("func", name, [new], stmt)
                # If old==new (PORDCH), flag ambiguous to prompt verification of FM version
                ambiguous = (new == name)
                _add_hit(issues, m.span("full"), "FunctionModule", name, suggested, note=note, ambiguous=ambiguous)

    # 3) Archiving reports *47 -> *70
    for m in ARCH47_RE.finditer(txt):
        name = m.group("name").upper()
        info = ARCHIVE_REPORT_MAP.get(name)
        if info:
            suggested = _mk_suggested_statement("report", name, [info["new"]], "SUBMIT")
            note = f"Archiving Object {info['obj']}: use *70 reports (EhP4+)."
            _add_hit(issues, m.span("full"), "Report", name, suggested, note=note)

    # 4) Very old *30 archiving reports -> replace by *70 family
    for m in ARCH30_RE.finditer(txt):
        name = m.group("name").upper()
        # steer to nearest *70 family if recognizable, else generic note
        family = name[:5]  # RM06E/RM06B/RM06I
        family_note = "Move to RM06**70 reports (EhP4+)."
        suggested = "SUBMIT <corresponding RM06**70 report>."
        _add_hit(issues, m.span("full"), "Report", name, suggested, note=family_note, ambiguous=True)

    # 5) Read programs (still usable) -> recommend SARI
    for m in READ_RE.finditer(txt):
        name = m.group("name").upper()
        note = "Read programs still usable; consider using Archive Information System (SARI)."
        suggested = "Use transaction SARI for displaying archived MM Purchasing documents."
        _add_hit(issues, m.span("full"), "Report", name, suggested, note=note, ambiguous=True)

    return issues


# -----------------------------
# API
# -----------------------------

@app.post("/remediate-mm-purchasing")
def remediate_mm_purchasing(units: List[Unit]):
    """
    Input: list of ABAP 'units' (programs/includes/fragments) with code.
    Output: same structure with appended 'mb_txn_usage' list of remediation suggestions.
    """
    results = []
    for u in units:
        src = u.code or ""
        issues = find_mm_purchasing_issues(src)

        obj = json.loads(u.model_dump_json())
        # IMPORTANT: to match your sample's structure/key name exactly:
        obj["mb_txn_usage"] = issues
        results.append(obj)

    return results
