import argparse
import sys
import os
import shlex
import storage_manager as sm


# ─────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────
def print_records(records: list):
    if not records:
        print("  (no records found)")
        return

    keys = list(dict.fromkeys(k for r in records for k in r))
    widths = {
        k: max(len(k), max(len(str(r.get(k, ""))) for r in records))
        for k in keys
    }

    sep    = "+-" + "-+-".join("-" * widths[k] for k in keys) + "-+"
    header = "| " + " | ".join(k.ljust(widths[k]) for k in keys) + " |"

    print(sep)
    print(header)
    print(sep)
    for r in records:
        print("| " + " | ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys) + " |")
    print(sep)
    print(f"  {len(records)} record(s)\n")


def hex_dump(text: str) -> str:
    lines = []
    n     = len(text)
    i     = 0
    last_was_padding = False

    while i < n:
        chunk = text[i:i + 16]
        if all(c == '_' for c in chunk) and 128 < i < n - 128:
            if not last_was_padding:
                lines.append("  *  (all underscores — free space padding)")
                last_was_padding = True
        else:
            last_was_padding = False
            hex_part  = " ".join(f"{ord(c):02x}" for c in chunk)
            char_part = "".join(c if 32 <= ord(c) < 127 else "." for c in chunk)
            lines.append(f"{i:04x}  {hex_part:<47}  |{char_part}|")
        i += 16
    return "\n".join(lines)


# ─────────────────────────────────────────────
# DEMO
# ─────────────────────────────────────────────
def run_demo(engine: dict):
    print("\n" + "=" * 60)
    print("  DEMO: Full Scenario")
    print("=" * 60)

    for db in ["shop_db", "hr_db"]:
        if db in sm.list_databases(engine):
            sm.drop_database(engine, db)

    sm.create_database(engine, "shop_db")
    sm.create_table(engine, "shop_db", "products", [
        {"name": "pid",   "type": "INT",     "size": 10},
        {"name": "name",  "type": "VARCHAR",  "size": 50},
        {"name": "price", "type": "FLOAT",    "size": 10},
        {"name": "stock", "type": "INT",      "size": 10},
    ])
    for p in [
        {"pid": "1", "name": "Widget A",    "price": "9.99",  "stock": "100"},
        {"pid": "2", "name": "Gadget B",    "price": "49.99", "stock": "50"},
        {"pid": "3", "name": "Doohickey",   "price": "4.50",  "stock": "200"},
        {"pid": "4", "name": "Thingamajig", "price": "15.00", "stock": "75"},
        {"pid": "5", "name": "Whatsit",     "price": "2.99",  "stock": "500"},
    ]:
        sm.insert_record(engine, "shop_db", "products", p)

    sm.create_database(engine, "hr_db")
    sm.create_table(engine, "hr_db", "employees", [
        {"name": "eid",        "type": "INT",     "size": 10},
        {"name": "first_name", "type": "VARCHAR",  "size": 30},
        {"name": "last_name",  "type": "VARCHAR",  "size": 30},
        {"name": "dept",       "type": "VARCHAR",  "size": 20},
        {"name": "salary",     "type": "FLOAT",    "size": 10},
    ])
    for e in [
        {"eid": "1", "first_name": "John", "last_name": "Doe",     "dept": "Engineering", "salary": "95000"},
        {"eid": "2", "first_name": "Jane", "last_name": "Smith",   "dept": "Marketing",   "salary": "78000"},
        {"eid": "3", "first_name": "Mike", "last_name": "Johnson", "dept": "Engineering", "salary": "105000"},
    ]:
        sm.insert_record(engine, "hr_db", "employees", e)

    print("\n  --- shop_db.products ---")
    print_records(sm.select_all(engine, "shop_db", "products"))
    print("  --- hr_db.employees ---")
    print_records(sm.select_all(engine, "hr_db", "employees"))

    rows = sm.select_all(engine, "shop_db", "products")
    sm.delete_record(engine, "shop_db", "products", rows[0]["_page"], rows[0]["_record_id"])
    sm.delete_record(engine, "shop_db", "products", rows[1]["_page"], rows[1]["_record_id"])

    print("\n  --- Page dump (before compact) ---")
    sm.dump_page(engine, "shop_db", "products", 0)
    sm.compact_page(engine, "shop_db", "products", 0)

    print("\n  --- After compact ---")
    print_records(sm.select_all(engine, "shop_db", "products"))

    rows   = sm.select_all(engine, "shop_db", "products")
    target = rows[0]
    sm.update_record(engine, "shop_db", "products",
                     target["_page"], target["_record_id"], {"price": "12.99"})

    print("\n  --- After update ---")
    print_records(sm.select_all(engine, "shop_db", "products"))
    sm.show_table_stats(engine, "shop_db", "products")
    print("  [DEMO COMPLETE]\n")


def print_wal_entries(engine, title="WAL Log"):
    entries = sm._read_wal(engine)
    print("\n" + "=" * 55)
    print(f"  {title}")
    print("=" * 55)
    if not entries:
        print("  (no WAL entries)")
        return entries
    for e in entries:
        print(f"  LSN={e['lsn']} OP={e['op']} DB={e['db']} TABLE={e['table']} PAGE_ID={e['page_id']} RECORD_ID={e['record_id']} DATA={e['data']}")
    print(f"  total_entries={len(entries)}")
    return entries


def print_page_summary(engine, db, table, page_id):
    pages = sm._load_pages(engine, db, table)
    page = next((p for p in pages if p["header"]["page_id"] == page_id), None)
    if page is None:
        print(f"[ERROR] Page {page_id} not found in {db}.{table}")
        return
    print(f"\n[PAGE] {db}.{table} page_id={page_id} page_lsn={page['header'].get('page_lsn', 0)} free_space={page['header'].get('free_space', 0)} record_count={page['header'].get('record_count', 0)}")
    for i, slot in enumerate(page["slots"]):
        record = page["records"][i] if slot.get("status") == "active" else {}
        print(f"  slot_{i:03d}: record_id={slot.get('record_id')}, status={slot.get('status')}, offset={slot.get('offset')}, length={slot.get('length')}, data={record}")


def write_unflushed_wal_insert(engine, db, table, data):
    pages = sm._load_pages(engine, db, table)
    page = pages[0]
    record_id = sum(len(p["slots"]) for p in pages)
    lsn = sm._next_lsn(engine)
    sm._write_wal(engine, lsn, "INSERT", db, table,
                  page["header"]["page_id"], record_id, data)
    return lsn, page["header"]["page_id"], record_id


def run_wal_recovery_scenario(engine: dict):
    print("\n" + "=" * 60)
    print("  WAL Write-Ahead / Crash Recovery Simulation")
    print("=" * 60)

    db_names = ["db_alpha", "db_bravo", "db_charlie"]
    tables   = ["customers", "orders"]
    columns  = [
        {"name": "id",   "type": "INT",     "size": 10},
        {"name": "name", "type": "VARCHAR", "size": 50},
    ]
    target_db    = db_names[0]
    target_table = tables[0]

    # Clean previous test databases
    for db in db_names:
        if db in sm.list_databases(engine):
            sm.drop_database(engine, db)

    # Create three databases each with two tables and a stable init record
    for db in db_names:
        sm.create_database(engine, db)
        for table in tables:
            sm.create_table(engine, db, table, columns)
            sm.insert_record(engine, db, table, {"id": "1", "name": "init"})

    print("\n[SETUP] Created 3 databases, each with 2 tables and an initial stable record.")

    # Create pending WAL-only inserts to simulate unflushed state in all tables
    pending_pages = []
    for db in db_names:
        for table in tables:
            pending_data = {"id": "99", "name": f"pending_{db}_{table}"}
            lsn, page_id, record_id = write_unflushed_wal_insert(engine, db, table, pending_data)
            pending_pages.append((db, table, page_id, record_id, lsn, pending_data))
    print("\n[SETUP] Wrote pending WAL-only entries for all tables (unflushed changes).")
    print_wal_entries(engine, "WAL after setup (pending unflushed entries)")

    # Execute 3 normal inserts on the target table and validate WAL after each one
    names = ["peter", "tom", "candy"]
    last_lsn = 0
    for idx, name in enumerate(names, start=1):
        record_data = {"id": str(idx + 1), "name": name}
        page_id, record_id = sm.insert_record(engine, target_db, target_table, record_data)
        entries = sm._read_wal(engine)
        last_entry = entries[-1]
        expected_lsn = last_lsn + 1

        print("\n[INSERT]", record_data)
        print(f"  target: {target_db}.{target_table} page_id={page_id} record_id={record_id}")
        print(f"  WAL last entry: LSN={last_entry['lsn']} PAGE_ID={last_entry['page_id']} RECORD_ID={last_entry['record_id']} DATA={last_entry['data']}")

        if last_entry['lsn'] != expected_lsn:
            print(f"  [ERROR] Unexpected LSN (expected {expected_lsn}).")
        if last_entry['page_id'] != page_id or last_entry['record_id'] != record_id or last_entry['data'] != record_data:
            print("  [ERROR] WAL entry content does not match the inserted record.")

        print_page_summary(engine, target_db, target_table, page_id)
        last_lsn = last_entry['lsn']

    # Execute a crash simulation insert on the target table
    crash_data = {"id": "999", "name": "555555555555"}
    print("\n[CRASH] Executing simulated crash insert (WAL write only, no page flush).")
    try:
        sm.insert_record(engine, target_db, target_table, crash_data, simulate_crash=True)
    except SystemExit:
        print("  [CRASH] SystemExit caught after WAL write.")

    print_wal_entries(engine, "WAL after simulated crash insert")

    print("\n[BEFORE RECOVERY] Pages with pending or inconsistent state:")
    print_page_summary(engine, target_db, target_table, 0)
    for db, table, page_id, record_id, lsn, pending_data in pending_pages:
        print(f"\n[UNFLUSHED] {db}.{table} page_id={page_id} pending LSN={lsn} record_id={record_id} data={pending_data}")
        print_page_summary(engine, db, table, page_id)

    # Recover from WAL
    print("\n[RECOVERY] Running WAL redo recovery...")
    sm.recover_from_wal(engine)

    print_wal_entries(engine, "WAL after recovery")
    print("\n[AFTER RECOVERY] Verified page state for target and pending pages:")
    print_page_summary(engine, target_db, target_table, 0)
    for db, table, page_id, record_id, lsn, pending_data in pending_pages:
        print(f"\n[RECOVERED] {db}.{table} page_id={page_id} pending LSN={lsn} record_id={record_id} data={pending_data}")
        print_page_summary(engine, db, table, page_id)

    print("\n[SCENARIO COMPLETE] WAL write-ahead and recovery path has been exercised.")


# ─────────────────────────────────────────────
# TEST SUITE
# ─────────────────────────────────────────────
def run_test_suite(engine: dict):
    print("\n" + "=" * 60)
    print("  TEST SUITE")
    print("=" * 60)

    passed = 0
    failed = 0

    def check(name, fn):
        nonlocal passed, failed
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {name}: {e}")
            failed += 1

    def cleanup():
        for db in sm.list_databases(engine):
            sm.drop_database(engine, db)

    def test_create():
        cleanup()
        assert sm.create_database(engine, "test_db")
        assert sm.create_table(engine, "test_db", "people", [
            {"name": "id",   "type": "INT",     "size": 10},
            {"name": "name", "type": "VARCHAR",  "size": 50},
            {"name": "age",  "type": "INT",      "size": 3},
        ])

    def test_insert():
        for r in [
            {"id": "1", "name": "Alice", "age": "30"},
            {"id": "2", "name": "Bob",   "age": "40"},
            {"id": "3", "name": "Carol", "age": "28"},
        ]:
            sm.insert_record(engine, "test_db", "people", r)
        assert len(sm.select_all(engine, "test_db", "people")) == 3

    def test_where():
        res = sm.select_where(engine, "test_db", "people", "age", "28")
        assert len(res) == 1 and res[0]["name"] == "Carol"

    def test_delete():
        rows = sm.select_all(engine, "test_db", "people")
        bob  = next(r for r in rows if r["name"] == "Bob")
        sm.delete_record(engine, "test_db", "people", bob["_page"], bob["_record_id"])
        assert all(r["name"] != "Bob" for r in sm.select_all(engine, "test_db", "people"))

    def test_update():
        rows  = sm.select_all(engine, "test_db", "people")
        alice = next(r for r in rows if r["id"] == "1")
        sm.update_record(engine, "test_db", "people",
                         alice["_page"], alice["_record_id"], {"age": "31"})
        updated = sm.select_where(engine, "test_db", "people", "id", "1")
        assert updated[0]["age"] == "31"

    def test_compact():
        pages   = sm._load_pages(engine, "test_db", "people")
        page_id = pages[0]["header"]["page_id"]
        removed = sm.compact_page(engine, "test_db", "people", page_id)
        assert removed >= 0

    def test_stats():
        sm.show_table_stats(engine, "test_db", "people")

    def test_wal():
        """WAL log should exist and contain entries after CRUD ops."""
        entries = sm._read_wal(engine)
        assert len(entries) > 0, "WAL should have at least one entry"
        assert all("lsn" in e for e in entries), "Every entry should have an LSN"

    def test_cleanup():
        assert sm.drop_table(engine, "test_db", "people")
        assert sm.drop_database(engine, "test_db")

    for name, fn in [
        ("Create",  test_create),
        ("Insert",  test_insert),
        ("Where",   test_where),
        ("Delete",  test_delete),
        ("Update",  test_update),
        ("Compact", test_compact),
        ("Stats",   test_stats),
        ("WAL",     test_wal),
        ("Cleanup", test_cleanup),
    ]:
        check(name, fn)

    print("\n" + "=" * 60)
    print(f"PASSED: {passed}")
    print(f"FAILED: {failed}")
    print("=" * 60)
    if failed:
        sys.exit(1)


# ─────────────────────────────────────────────
# INTERACTIVE MENU
# ─────────────────────────────────────────────
def interactive_menu(engine: dict):
    current_db = None

    while True:
        print("\n" + "=" * 55)
        print(" [*] Relational Database Storage Module Test" + "\n")
        print(f"  Current active database : {current_db if current_db else '(None)'}")
        print("-" * 55)
        print("  1.  Set Active Database / Initialize Environment")
        print("-" * 20, "CRUD Operations", "-" * 20)
        print("  2.  Create Table")
        print("  3.  Insert Record  (Normal)")
        print("  4.  Insert Record  ([!!] Simulate Power Failure Crash)")
        print("  5.  Select All Records")
        print("  6.  Update Record")
        print("  7.  Delete Record")
        print("  8.  Compact Page")
        print("  9.  Show Table Stats")
        print("  10. Dump Page  (Hex Dump + Slot Status)")
        print("-" * 20, "WAL Log", "-" * 20)
        print("  11. View WAL Log File  (wallog.txt)")
        print("  12. Run Disaster Recovery  (Manual Redo from WAL)")
        print("  13. Run Automated Test Suite")
        print("  14. WAL Write-Ahead / Crash Recovery Scenario")
        print("  0. Exit")
        print("=" * 55)

        choice = input("Please select a function: ").strip()

        if choice == "1":
            db_name = input("Enter database name to create/select: ").strip()
            if not db_name:
                print("[ERROR] Database name cannot be empty.")
                continue
            if db_name not in sm.list_databases(engine):
                sm.create_database(engine, db_name)
            else:
                print(f"[INFO] Database '{db_name}' already exists.")
            current_db = db_name
            print(f"[OK] Active database set to '{current_db}'")

        elif choice == "2":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name  = input("Enter table name: ").strip()
            columns_str = input("Enter columns  (e.g. pid:INT:10,name:VARCHAR:50): ").strip()
            try:
                columns = []
                for col in columns_str.split(","):
                    n, t, s = col.split(":")
                    columns.append({"name": n.strip(), "type": t.strip().upper(), "size": int(s.strip())})
                sm.create_table(engine, current_db, table_name, columns)
            except Exception as e:
                print(f"[ERROR] Invalid columns format: {e}")

        elif choice == "3":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            record_str = input("Enter fields  (e.g. pid=1 name=Alice price=9.99): ").strip()
            try:
                data = {k: v for k, v in (f.split("=", 1) for f in shlex.split(record_str) if "=" in f)}
                if not data:
                    print("[ERROR] No valid key=value fields provided.")
                    continue
                res = sm.insert_record(engine, current_db, table_name, data)
                if res:
                    print(f"[OK] Record inserted — Page ID: {res[0]}, Record ID: {res[1]}")
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "4":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            record_str = input("Enter fields  (e.g. pid=99 name=Ghost price=0.01): ").strip()
            try:
                data = {k: v for k, v in (f.split("=", 1) for f in shlex.split(record_str) if "=" in f)}
                if not data:
                    print("[ERROR] No valid key=value fields provided.")
                    continue
                print("[CRASH SIM] Writing to WAL then killing process before page flush...")
                sm.insert_record(engine, current_db, table_name, data, simulate_crash=True)
            except SystemExit:
                pass
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "5":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                print_records(sm.select_all(engine, current_db, table_name))
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "6":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                page_id   = int(input("Enter Page ID: ").strip())
                record_id = int(input("Enter Record ID: ").strip())
                field_str = input("Enter fields to update  (e.g. price=12.99): ").strip()
                new_data  = {k: v for k, v in (f.split("=", 1) for f in shlex.split(field_str) if "=" in f)}
                if not new_data:
                    print("[ERROR] No fields to update.")
                    continue
                if sm.update_record(engine, current_db, table_name, page_id, record_id, new_data):
                    print("[OK] Record updated.")
                else:
                    print("[ERROR] Update failed (record not found or insufficient page space).")
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "7":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                page_id   = int(input("Enter Page ID: ").strip())
                record_id = int(input("Enter Record ID: ").strip())
                if sm.delete_record(engine, current_db, table_name, page_id, record_id):
                    print("[OK] Record deleted.")
                else:
                    print("[ERROR] Record not found or already deleted.")
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "8":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                page_id = int(input("Enter Page ID: ").strip())
                removed = sm.compact_page(engine, current_db, table_name, page_id)
                print(f"[OK] Page compacted — {removed} deleted slot(s) cleared.")
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "9":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                sm.show_table_stats(engine, current_db, table_name)
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "10":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            try:
                page_id = int(input("Enter Page ID: ").strip())
                sm.dump_page(engine, current_db, table_name, page_id)

                path = sm._table_path(engine, current_db, table_name)
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    pages = raw.split(sm.PAGE_SEPARATOR)
                    if 0 <= page_id < len(pages):
                        print("\n--- HEX DUMP ---")
                        print(hex_dump(pages[page_id]))
            except Exception as e:
                print(f"[ERROR] {e}")

        elif choice == "11":
            sm.view_wal(engine)

        elif choice == "12":
            print("\n[RECOVERY] Running manual WAL Redo recovery...")
            sm.recover_from_wal(engine)

        elif choice == "13":
            run_test_suite(engine)
            
        elif choice == "14":
            run_wal_recovery_scenario(engine)
            
        elif choice == "0":
            print("[INFO] Exiting. Goodbye!")
            break

        else:
            print("[ERROR] Invalid choice. Please select 1–13.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",   default="./MyTinyDB")
    parser.add_argument("command",  nargs="?", default=None)
    args   = parser.parse_args()

    engine = sm.init_storage(args.root)

    # ── Automatic startup WAL recovery ──────────────────────
    print("[STARTUP] Checking WAL for unrecovered transactions...")
    sm.recover_from_wal(engine)

    if args.command == "demo":
        run_demo(engine)
    elif args.command == "test":
        run_test_suite(engine)
    elif args.command == "wal":
        run_wal_recovery_scenario(engine)
    elif args.command is None:
        interactive_menu(engine)
    else:
        print(f"Unknown command '{args.command}'. Use: demo | test | wal | (no arg for menu)")


if __name__ == "__main__":
    main()
