import json
import os
import sys
import time

NULL_VALUE = "__NULL__"
PAGE_SEPARATOR = "\n<<<PAGE_BOUNDARY>>>\n"
WAL_FILE = "wallog.txt"


def init_storage(root_dir: str) -> dict:
    os.makedirs(root_dir, exist_ok=True)

    config_path = os.path.join(root_dir, "storage_config.json")
    if not os.path.exists(config_path):
        config = _default_config()
        _write_json(config_path, config)
    else:
        config = _read_json(config_path)
        defaults = _default_config()
        updated = False
        for k, v in defaults.items():
            if k not in config:
                config[k] = v
                updated = True
        if updated:
            _write_json(config_path, config)

    catalog_path = os.path.join(root_dir, "system_catalog.json")
    if not os.path.exists(catalog_path):
        _write_json(catalog_path, {"databases": {}})

    return {"root": root_dir, "config": config}


def _default_config():
    return {
        "PAGE_SIZE": 4096,
        "RECORD_FIXED_SIZE": 128,
        "MAX_SLOTS_PER_PAGE": 32,
        "VERSION": "1.0",
        "CREATED_AT": _now(),
    }


def create_database(engine, db_name):
    catalog = _load_catalog(engine)
    if db_name in catalog["databases"]:
        print(f"[ERROR] Database '{db_name}' already exists.")
        return False

    os.makedirs(os.path.join(engine["root"], db_name), exist_ok=True)

    catalog["databases"][db_name] = {
        "created_at": _now(),
        "tables": {}
    }

    _save_catalog(engine, catalog)
    print(f"[OK] Database '{db_name}' created.")
    return True


def drop_database(engine, db_name):
    catalog = _load_catalog(engine)
    if db_name not in catalog["databases"]:
        return False

    import shutil
    shutil.rmtree(os.path.join(engine["root"], db_name), ignore_errors=True)

    del catalog["databases"][db_name]
    _save_catalog(engine, catalog)

    print(f"[OK] Database '{db_name}' dropped.")
    return True


def list_databases(engine):
    return list(_load_catalog(engine)["databases"].keys())

def create_table(engine, db_name, table_name, columns):
    catalog = _load_catalog(engine)

    if db_name not in catalog["databases"]:
        return False

    tables = catalog["databases"][db_name]["tables"]
    if table_name in tables:
        return False

    tables[table_name] = {
        "columns": columns,
        "record_count": 0,
        "page_count": 1,
        "created_at": _now()
    }

    _save_catalog(engine, catalog)

    page = _new_page(0)
    _write_table_file(engine, db_name, table_name, [page], columns)

    print(f"[OK] Table '{db_name}.{table_name}' created.")
    return True

def drop_table(engine, db_name, table_name):
    catalog = _load_catalog(engine)

    if db_name not in catalog["databases"]:
        return False

    if table_name not in catalog["databases"][db_name]["tables"]:
        return False

    del catalog["databases"][db_name]["tables"][table_name]
    _save_catalog(engine, catalog)

    path = _table_path(engine, db_name, table_name)
    if os.path.exists(path):
        os.remove(path)

    print(f"[OK] Table '{db_name}.{table_name}' dropped.")
    return True

def list_tables(engine, db_name):
    catalog = _load_catalog(engine)
    if db_name not in catalog["databases"]:
        return []
    return list(catalog["databases"][db_name]["tables"].keys())

def _new_page(page_id: int):
    return {
        "header": {
            "page_id":     page_id,
            "free_space":  4096,
            "slot_count":  0,
            "record_count": 0,
            "page_lsn":    0,       # LSN of the last write flushed to this page
        },
        "slots":   [],
        "records": []
    }

def _format_record(record: dict, columns: list, offset: int = 0) -> str:
    fields = " | ".join(
        f"{c['name']}={record.get(c['name'], NULL_VALUE)}"
        for c in columns
    )
    return f"@{offset:06d} {fields}\n"

def _build_header(header):
    return (
        "[HEADER]\n"
        f"page_id={header['page_id']:06d}\n"
        f"free_space={header['free_space']:06d}\n"
        f"slot_count={header['slot_count']:04d}\n"
        f"record_count={header['record_count']:04d}\n"
        f"page_lsn={header.get('page_lsn', 0):010d}\n"
        "[/HEADER]\n"
    )

def _build_slots(slots):
    out = ["[SLOTS]\n"]
    for i, s in enumerate(slots):
        rid = s.get('record_id', i)
        out.append(
            f"slot_{i:03d}: record_id={rid:06d}, offset={s['offset']:06d}, length={s['length']:06d}, status={s['status']}\n"
        )
    out.append("[/SLOTS]\n")
    return "".join(out)

def _compute_free_space(page, engine):
    header_len = len(_build_header(page["header"]))
    slots_len = len(_build_slots(page["slots"]))
    active_records_len = sum(s["length"] for s in page["slots"] if s["status"] == "active")
    return max(engine["config"]["PAGE_SIZE"] - header_len - slots_len - active_records_len, 0)

def _format_page(page, engine, columns):
    free_space = _compute_free_space(page, engine)
    page["header"]["free_space"] = free_space

    hdr_text   = _build_header(page["header"])
    slots_text = _build_slots(page["slots"])

    PAGE_SIZE  = engine["config"]["PAGE_SIZE"]
    page_chars = list("_" * PAGE_SIZE)

    # Header + Slots at the top
    page_chars[0:len(hdr_text)] = list(hdr_text)
    slots_start = len(hdr_text)
    page_chars[slots_start:slots_start + len(slots_text)] = list(slots_text)

    # Active records at their physical offsets (bottom-up)
    for slot, record in zip(page["slots"], page["records"]):
        if slot["status"] == "active":
            offset  = slot["offset"]
            length  = slot["length"]
            rec_text = _format_record(record, columns, offset)
            page_chars[offset:offset + length] = list(rec_text)

    return "".join(page_chars)

def _wal_path(engine) -> str:
    return os.path.join(engine["root"], WAL_FILE)

def _next_lsn(engine) -> int:
    """Return the next available LSN (1-based, monotonically increasing)."""
    path = _wal_path(engine)
    if not os.path.exists(path):
        return 1
    last = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("LSN="):
                try:
                    lsn = int(line.split("=", 1)[1])
                    if lsn > last:
                        last = lsn
                except ValueError:
                    pass
    return last + 1


def _write_wal(engine, lsn: int, operation: str, db: str, table: str,
               page_id: int, record_id, data: dict):
    """
    Append one WAL entry to wallog.txt.

    Entry format (readable / line-based):
        LSN=<lsn>
        OP=<INSERT|UPDATE|DELETE>
        DB=<db>  TABLE=<table>
        PAGE_ID=<page_id>  RECORD_ID=<record_id>
        DATA=<json>
        TIMESTAMP=<iso>
        ---
    """
    path = _wal_path(engine)
    entry = (
        f"LSN={lsn}\n"
        f"OP={operation}\n"
        f"DB={db}  TABLE={table}\n"
        f"PAGE_ID={page_id}  RECORD_ID={record_id}\n"
        f"DATA={json.dumps(data)}\n"
        f"TIMESTAMP={_now()}\n"
        f"---\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


def _read_wal(engine) -> list:
    """
    Parse wallog.txt and return a list of log entry dicts:
        { lsn, op, db, table, page_id, record_id, data, timestamp }
    """
    path = _wal_path(engine)
    if not os.path.exists(path):
        return []

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    for block in raw.strip().split("---"):
        block = block.strip()
        if not block:
            continue
        entry = {}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("LSN="):
                entry["lsn"] = int(line.split("=", 1)[1])
            elif line.startswith("OP="):
                entry["op"] = line.split("=", 1)[1].strip()
            elif line.startswith("DB="):
                parts = line.split()
                entry["db"]    = parts[0].split("=", 1)[1]
                entry["table"] = parts[1].split("=", 1)[1]
            elif line.startswith("PAGE_ID="):
                parts = line.split()
                entry["page_id"]   = int(parts[0].split("=", 1)[1])
                entry["record_id"] = int(parts[1].split("=", 1)[1])
            elif line.startswith("DATA="):
                entry["data"] = json.loads(line.split("=", 1)[1])
            elif line.startswith("TIMESTAMP="):
                entry["timestamp"] = line.split("=", 1)[1].strip()
        if entry:
            entries.append(entry)
    return entries


def view_wal(engine):
    """Print the raw WAL log to console."""
    path = _wal_path(engine)
    if not os.path.exists(path):
        print("[WAL] No WAL log file found. Nothing to show.")
        return
    print(f"\n{'='*55}")
    print(f"  WAL Log File: {path}")
    print(f"{'='*55}")
    with open(path, "r", encoding="utf-8") as f:
        print(f.read())


def recover_from_wal(engine):
    """
    Startup Redo Recovery.

    For each WAL entry, compare the entry's LSN against the target
    page's page_lsn. If LSN > page_lsn, the operation did not make
    it to disk and must be replayed (Redo).
    """
    entries = _read_wal(engine)
    if not entries:
        print("[RECOVERY] WAL is empty. No recovery needed.")
        return

    print(f"\n[RECOVERY] Starting WAL Redo recovery — {len(entries)} log entry(ies) found.")
    redone = 0

    catalog = _load_catalog(engine)

    for entry in entries:
        lsn       = entry["lsn"]
        op        = entry["op"]
        db        = entry["db"]
        table     = entry["table"]
        page_id   = entry["page_id"]
        record_id = entry["record_id"]
        data      = entry["data"]

        # Skip if database or table no longer exists
        if db not in catalog["databases"]:
            continue
        if table not in catalog["databases"][db]["tables"]:
            continue

        # Load the page and check page_lsn
        pages = _load_pages(engine, db, table)
        page  = next((p for p in pages if p["header"]["page_id"] == page_id), None)
        if page is None:
            continue

        page_lsn = page["header"].get("page_lsn", 0)
        if lsn <= page_lsn:
            print(f"  [SKIP]  LSN={lsn} already applied (page_lsn={page_lsn}).")
            continue

        # ── REDO the operation ──────────────────────────────
        cols = catalog["databases"][db]["tables"][table]["columns"]

        if op == "INSERT":
            rec_len = len(_format_record(data, cols, 0)) - 1
            active_offsets = [s["offset"] for s in page["slots"] if s["status"] == "active"]
            lowest_offset  = min(active_offsets) if active_offsets else engine["config"]["PAGE_SIZE"]
            new_offset     = lowest_offset - rec_len

            # Check if slot with same record_id already exists (idempotent)
            existing_slot = next((s for s in page["slots"] if s["record_id"] == record_id), None)
            if existing_slot and existing_slot["status"] == "active":
                print(f"  [SKIP]  LSN={lsn} INSERT record_id={record_id} already on page.")
                continue

            if existing_slot and existing_slot["status"] == "deleted":
                # Reuse the deleted slot
                idx = page["slots"].index(existing_slot)
                existing_slot["offset"] = new_offset
                existing_slot["length"] = rec_len
                existing_slot["status"] = "active"
                page["records"][idx]    = data
            else:
                page["slots"].append({
                    "record_id": record_id,
                    "offset":    new_offset,
                    "length":    rec_len,
                    "status":    "active"
                })
                page["records"].append(data)
                page["header"]["slot_count"]   += 1

            page["header"]["record_count"] += 1
            catalog["databases"][db]["tables"][table]["record_count"] += 1

        elif op == "DELETE":
            slot = next((s for s in page["slots"] if s["record_id"] == record_id), None)
            if slot and slot["status"] == "active":
                idx = page["slots"].index(slot)
                slot["status"] = "deleted"
                slot["offset"] = 0
                slot["length"] = 0
                page["records"][idx] = {}
                page["header"]["record_count"] = max(0, page["header"]["record_count"] - 1)
                catalog["databases"][db]["tables"][table]["record_count"] = max(
                    0, catalog["databases"][db]["tables"][table]["record_count"] - 1)

        elif op == "UPDATE":
            slot = next((s for s in page["slots"] if s["record_id"] == record_id), None)
            if slot and slot["status"] == "active":
                idx          = page["slots"].index(slot)
                updated_data = dict(page["records"][idx])
                updated_data.update(data)
                new_rec_len  = len(_format_record(updated_data, cols, 0)) - 1

                # Compact and place at bottom
                current_offset = engine["config"]["PAGE_SIZE"]
                slot["status"] = "deleted"
                slot["offset"] = 0
                slot["length"] = 0
                page["records"][idx] = {}
                for j, s2 in enumerate(page["slots"]):
                    if s2["status"] == "active":
                        current_offset -= s2["length"]
                        s2["offset"] = current_offset

                new_offset     = current_offset - new_rec_len
                slot["offset"] = new_offset
                slot["length"] = new_rec_len
                slot["status"] = "active"
                page["records"][idx] = updated_data

        # Update page_lsn and flush to disk
        page["header"]["page_lsn"]   = lsn
        page["header"]["free_space"] = _compute_free_space(page, engine)
        _save_pages(engine, db, table, pages)
        _save_catalog(engine, catalog)

        print(f"  [REDO]  LSN={lsn} {op} on {db}.{table} page={page_id} record={record_id}")
        redone += 1

    print(f"[RECOVERY] Done. {redone} operation(s) redone.\n")


# ─────────────────────────────────────────────
# RECORD OPS
# ─────────────────────────────────────────────

def insert_record(engine, db, table, data, simulate_crash=False):
    catalog = _load_catalog(engine)
    if db not in catalog["databases"] or table not in catalog["databases"][db]["tables"]:
        print(f"[ERROR] Table '{db}.{table}' does not exist.")
        return None

    cols = catalog["databases"][db]["tables"][table]["columns"]
    for c in cols:
        data.setdefault(c["name"], NULL_VALUE)

    table_meta = catalog["databases"][db]["tables"][table]
    pages      = _load_pages(engine, db, table)

    rec_len           = len(_format_record(data, cols, 0)) - 1
    
    target            = None
    reused_slot_idx   = -1

    for p in pages:
        has_deleted_slot = False
        deleted_idx      = -1
        for idx, s in enumerate(p["slots"]):
            if s["status"] == "deleted":
                has_deleted_slot = True
                deleted_idx      = idx
                break

        needed_space = rec_len
        if not has_deleted_slot:
            needed_space += 73

        if p["header"]["free_space"] >= needed_space and (
            len(p["slots"]) < engine["config"]["MAX_SLOTS_PER_PAGE"] or has_deleted_slot
        ):
            target = p
            if has_deleted_slot:
                reused_slot_idx = deleted_idx
            break

    if not target:
        target = _new_page(len(pages))
        pages.append(target)
        table_meta["page_count"] = len(pages)
        reused_slot_idx = -1

    active_offsets = [s["offset"] for s in target["slots"] if s["status"] == "active"]
    lowest_offset  = min(active_offsets) if active_offsets else engine["config"]["PAGE_SIZE"]
    new_offset     = lowest_offset - rec_len

    if reused_slot_idx != -1:
        slot      = target["slots"][reused_slot_idx]
        record_id = slot["record_id"]
        slot["offset"] = new_offset
        slot["length"] = rec_len
        slot["status"] = "active"
        target["records"][reused_slot_idx] = data
    else:
        record_id = sum(len(p["slots"]) for p in pages)
        target["slots"].append({
            "record_id": record_id,
            "offset":    new_offset,
            "length":    rec_len,
            "status":    "active"
        })
        target["records"].append(data)
        target["header"]["slot_count"] += 1

    target["header"]["record_count"] += 1
    table_meta["record_count"]       += 1

    # ── WAL: write BEFORE flushing to disk ──────────────────
    lsn = _next_lsn(engine)
    _write_wal(engine, lsn, "INSERT", db, table,
               target["header"]["page_id"], record_id, data)

    if simulate_crash:
        print(f"[WAL] LSN={lsn} INSERT logged to WAL.")
        print("[CRASH] [!!] Simulated power failure! Process killed before page flush.")
        sys.exit(0)

    # ── Flush to disk ────────────────────────────────────────
    target["header"]["page_lsn"]   = lsn
    target["header"]["free_space"] = _compute_free_space(target, engine)
    _save_catalog(engine, catalog)
    _save_pages(engine, db, table, pages)

    return (target["header"]["page_id"], record_id)


def select_all(engine, db, table):
    results = []
    for page in _load_pages(engine, db, table):
        for i, slot in enumerate(page["slots"]):
            if slot.get("status") != "active":
                continue
            record = {"_page": page["header"]["page_id"], "_record_id": slot.get("record_id")}
            record.update(page["records"][i])
            results.append(record)
    return results


def delete_record(engine, db, table, page_id, record_id, simulate_crash=False):
    catalog = _load_catalog(engine)
    if db not in catalog["databases"] or table not in catalog["databases"][db]["tables"]:
        return False

    table_meta = catalog["databases"][db]["tables"][table]
    pages      = _load_pages(engine, db, table)

    for page in pages:
        if page["header"]["page_id"] == page_id:
            for i, s in enumerate(page["slots"]):
                if s.get("record_id") == record_id and s.get("status") == "active":
                    deleted_data = page["records"][i]

                    # WAL first
                    lsn = _next_lsn(engine)
                    _write_wal(engine, lsn, "DELETE", db, table,
                               page_id, record_id, deleted_data)

                    if simulate_crash:
                        print(f"[WAL] LSN={lsn} DELETE logged to WAL.")
                        print("[CRASH] [!!] Simulated power failure!")
                        sys.exit(0)

                    s["status"] = "deleted"
                    s["offset"] = 0
                    s["length"] = 0
                    page["records"][i] = {}
                    page["header"]["record_count"] = max(0, page["header"]["record_count"] - 1)
                    table_meta["record_count"]     = max(0, table_meta["record_count"] - 1)
                    page["header"]["page_lsn"]     = lsn
                    page["header"]["free_space"]   = _compute_free_space(page, engine)
                    _save_pages(engine, db, table, pages)
                    _save_catalog(engine, catalog)
                    return True
    return False


def update_record(engine, db, table, page_id, record_id, new_data, simulate_crash=False):
    catalog = _load_catalog(engine)
    if db not in catalog["databases"] or table not in catalog["databases"][db]["tables"]:
        return False

    cols  = catalog["databases"][db]["tables"][table]["columns"]
    pages = _load_pages(engine, db, table)

    for page in pages:
        if page["header"]["page_id"] == page_id:
            for i, s in enumerate(page["slots"]):
                if s.get("record_id") == record_id and s.get("status") == "active":
                    old_data   = page["records"][i]
                    old_offset = s["offset"]
                    old_length = s["length"]
                    old_status = s["status"]

                    updated_data = dict(old_data)
                    updated_data.update(new_data)
                    new_rec_len = len(_format_record(updated_data, cols, 0)) - 1

                    # Temporarily mark deleted to check free space
                    s["status"]      = "deleted"
                    s["offset"]      = 0
                    s["length"]      = 0
                    page["records"][i] = {}

                    free_space = _compute_free_space(page, engine)
                    if free_space >= new_rec_len:
                        # WAL first
                        lsn = _next_lsn(engine)
                        _write_wal(engine, lsn, "UPDATE", db, table,
                                   page_id, record_id, new_data)

                        if simulate_crash:
                            print(f"[WAL] LSN={lsn} UPDATE logged to WAL.")
                            print("[CRASH] [!!] Simulated power failure!")
                            sys.exit(0)

                        # Compact and place updated record at bottom
                        current_offset = engine["config"]["PAGE_SIZE"]
                        for idx, slot in enumerate(page["slots"]):
                            if slot["status"] == "active":
                                current_offset -= slot["length"]
                                slot["offset"]  = current_offset

                        new_offset     = current_offset - new_rec_len
                        s["offset"]    = new_offset
                        s["length"]    = new_rec_len
                        s["status"]    = "active"
                        page["records"][i]     = updated_data
                        page["header"]["page_lsn"]   = lsn
                        page["header"]["free_space"]  = _compute_free_space(page, engine)
                        _save_pages(engine, db, table, pages)
                        return True
                    else:
                        # Rollback
                        s["offset"]      = old_offset
                        s["length"]      = old_length
                        s["status"]      = old_status
                        page["records"][i] = old_data
                        return False
    return False


def compact_page(engine, db, table, page_id):
    pages = _load_pages(engine, db, table)
    page  = next((p for p in pages if p["header"]["page_id"] == page_id), None)
    if not page:
        return 0

    removed        = 0
    current_offset = engine["config"]["PAGE_SIZE"]

    for i, slot in enumerate(page["slots"]):
        if slot["status"] == "active":
            current_offset -= slot["length"]
            slot["offset"]  = current_offset
        elif slot["status"] == "deleted":
            slot["offset"]    = 0
            slot["length"]    = 0
            page["records"][i] = {}
            removed += 1

    page["header"]["record_count"] = sum(1 for s in page["slots"] if s["status"] == "active")
    page["header"]["free_space"]   = _compute_free_space(page, engine)
    _save_pages(engine, db, table, pages)
    return removed


def select_where(engine, db, table, column, value):
    rows     = select_all(engine, db, table)
    matching = []
    for r in rows:
        record_data = r.get("data", r)
        if record_data.get(column) == value:
            matching.append(r)
    return matching


def dump_page(engine, db, table, page_id):
    pages = _load_pages(engine, db, table)
    page  = next((p for p in pages if p["header"]["page_id"] == page_id), None)
    if page is None:
        print(f"[ERROR] Page {page_id} not found in {db}.{table}")
        return

    print(f"[PAGE] {db}.{table} page_id={page_id}")
    print("[HEADER]")
    for k, v in page["header"].items():
        print(f"  {k}={v}")
    print("[RECORDS]")
    for i, (slot, record) in enumerate(zip(page["slots"], page["records"])):
        print(f"  slot_{i:03d}: {slot}")
        print(f"    record={record}")
    print("[SLOTS]")
    for i, slot in enumerate(page["slots"]):
        print(f"  slot_{i:03d}: record_id={slot.get('record_id')}, offset={slot.get('offset')}, "
              f"length={slot.get('length')}, status={slot.get('status')}")


def show_table_stats(engine, db, table):
    catalog = _load_catalog(engine)
    if db not in catalog["databases"] or table not in catalog["databases"][db]["tables"]:
        print(f"[ERROR] Table {db}.{table} not found.")
        return

    pages          = _load_pages(engine, db, table)
    page_count     = len(pages)
    record_count   = sum(1 for page in pages for slot in page["slots"] if slot.get("status") == "active")
    deleted_count  = sum(1 for page in pages for slot in page["slots"] if slot.get("status") == "deleted")
    free_space     = sum(page["header"].get("free_space", 0) for page in pages)
    max_page_lsn   = max((page["header"].get("page_lsn", 0) for page in pages), default=0)

    print(f"[STATS] {db}.{table}")
    print(f"  pages={page_count}")
    print(f"  active_records={record_count}")
    print(f"  deleted_records={deleted_count}")
    print(f"  free_space={free_space}")
    print(f"  max_page_lsn={max_page_lsn}")


# ─────────────────────────────────────────────
# FILE OPS
# ─────────────────────────────────────────────

def _table_path(engine, db, table):
    return os.path.join(engine["root"], db, f"{table}.txt")


def _write_table_file(engine, db, table, pages, cols):
    with open(_table_path(engine, db, table), "w", encoding="utf-8") as f:
        f.write(PAGE_SEPARATOR.join(
            _format_page(p, engine, cols) for p in pages
        ))


def _load_pages(engine, db, table):
    with open(_table_path(engine, db, table), "r", encoding="utf-8") as f:
        raw = f.read()
    return [_parse_page(x) for x in raw.split(PAGE_SEPARATOR)]


def _save_pages(engine, db, table, pages):
    catalog = _load_catalog(engine)
    cols    = catalog["databases"][db]["tables"][table]["columns"]
    _write_table_file(engine, db, table, pages, cols)


def _parse_page(text):
    page = {"header": {}, "slots": [], "records": []}

    header_start = text.find("[HEADER]")
    header_end   = text.find("[/HEADER]")
    if header_start == -1 or header_end == -1:
        return page

    header_section = text[header_start:header_end]
    for line in header_section.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k not in ("page_id", "free_space", "slot_count", "record_count", "page_lsn"):
                continue
            try:
                page["header"][k] = int(v.strip())
            except ValueError:
                pass

    slots_start = text.find("[SLOTS]", header_end)
    slots_end   = text.find("[/SLOTS]", slots_start)
    if slots_start != -1 and slots_end != -1:
        slots_section = text[slots_start:slots_end]
        for line in slots_section.splitlines():
            line = line.strip()
            if line.startswith("slot_"):
                parts = line.split(":", 1)
                if len(parts) < 2:
                    continue
                slot_data = parts[1].strip().split(",")
                slot = {}
                for item in slot_data:
                    if "=" not in item:
                        continue
                    k, v = item.split("=", 1)
                    k = k.strip(); v = v.strip()
                    if k in ("record_id", "offset", "length"):
                        slot[k] = int(v)
                    else:
                        slot[k] = v
                page["slots"].append(slot)

    page["records"] = [{} for _ in range(len(page["slots"]))]
    for i, slot in enumerate(page["slots"]):
        if slot.get("status") == "active":
            offset  = slot["offset"]
            length  = slot["length"]
            rec_str = text[offset:offset + length].strip()
            if rec_str.startswith("@") and " " in rec_str:
                content = rec_str.split(" ", 1)[1]
                data    = {}
                for f in content.split("|"):
                    if "=" in f:
                        k, v = f.split("=", 1)
                        data[k.strip()] = v.strip()
                page["records"][i] = data
        else:
            page["records"][i] = {}

    return page


# ─────────────────────────────────────────────
# CATALOG
# ─────────────────────────────────────────────

def _load_catalog(engine):
    return _read_json(os.path.join(engine["root"], "system_catalog.json"))


def _save_catalog(engine, catalog):
    _write_json(os.path.join(engine["root"], "system_catalog.json"), catalog)


def _read_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
