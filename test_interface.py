import argparse
import sys
import os
import shlex
import storage_manager as sm


# ─────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────
def print_records(records: list[dict]):
    if not records:
        print("  (no records found)")
        return

    keys = list(dict.fromkeys(k for r in records for k in r))
    widths = {
        k: max(len(k), max(len(str(r.get(k, ""))) for r in records))
        for k in keys
    }

    sep = "+-" + "-+-".join("-" * widths[k] for k in keys) + "-+"
    header = "| " + " | ".join(k.ljust(widths[k]) for k in keys) + " |"

    print(sep)
    print(header)
    print(sep)

    for r in records:
        print("| " + " | ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys) + " |")

    print(sep)
    print(f"  {len(records)} record(s)\n")


def hex_dump(text: str):
    lines = []
    n = len(text)
    i = 0
    last_was_underscore_line = False
    
    while i < n:
        chunk = text[i:i+16]
        if all(c == '_' for c in chunk) and i > 128 and i < n - 128:
            if not last_was_underscore_line:
                lines.append("  *  (all underscores padding)")
                last_was_underscore_line = True
        else:
            last_was_underscore_line = False
            hex_part = " ".join(f"{ord(c):02x}" for c in chunk)
            char_part = "".join(c if (32 <= ord(c) < 127) else "." for c in chunk)
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

    # clean old data
    for db in ["shop_db", "hr_db"]:
        if db in sm.list_databases(engine):
            sm.drop_database(engine, db)

    # ───── shop_db ─────
    sm.create_database(engine, "shop_db")

    sm.create_table(engine, "shop_db", "products", [
        {"name": "pid", "type": "INT", "size": 10},
        {"name": "name", "type": "VARCHAR", "size": 50},
        {"name": "price", "type": "FLOAT", "size": 10},
        {"name": "stock", "type": "INT", "size": 10},
    ])

    products = [
        {"pid": "1", "name": "Widget A", "price": "9.99", "stock": "100"},
        {"pid": "2", "name": "Gadget B", "price": "49.99", "stock": "50"},
        {"pid": "3", "name": "Doohickey", "price": "4.50", "stock": "200"},
        {"pid": "4", "name": "Thingamajig", "price": "15.00", "stock": "75"},
        {"pid": "5", "name": "Whatsit", "price": "2.99", "stock": "500"},
    ]

    for p in products:
        sm.insert_record(engine, "shop_db", "products", p)

    # ───── hr_db ─────
    sm.create_database(engine, "hr_db")

    sm.create_table(engine, "hr_db", "employees", [
        {"name": "eid", "type": "INT", "size": 10},
        {"name": "first_name", "type": "VARCHAR", "size": 30},
        {"name": "last_name", "type": "VARCHAR", "size": 30},
        {"name": "dept", "type": "VARCHAR", "size": 20},
        {"name": "salary", "type": "FLOAT", "size": 10},
    ])

    employees = [
        {"eid": "1", "first_name": "John", "last_name": "Doe", "dept": "Engineering", "salary": "95000"},
        {"eid": "2", "first_name": "Jane", "last_name": "Smith", "dept": "Marketing", "salary": "78000"},
        {"eid": "3", "first_name": "Mike", "last_name": "Johnson", "dept": "Engineering", "salary": "105000"},
    ]

    for e in employees:
        sm.insert_record(engine, "hr_db", "employees", e)

    # ───── SELECT ─────
    print("\n  --- shop_db.products ---")
    print_records(sm.select_all(engine, "shop_db", "products"))

    print("  --- hr_db.employees ---")
    print_records(sm.select_all(engine, "hr_db", "employees"))

    # ───── DELETE (FIXED record_id) ─────
    rows = sm.select_all(engine, "shop_db", "products")

    r1 = rows[0]
    r2 = rows[1]

    sm.delete_record(engine, "shop_db", "products", r1["_page"], r1["_record_id"])
    sm.delete_record(engine, "shop_db", "products", r2["_page"], r2["_record_id"])

    print("\n  --- Page dump (before compact) ---")
    sm.dump_page(engine, "shop_db", "products", 0)

    # ───── COMPACT ─────
    sm.compact_page(engine, "shop_db", "products", 0)

    print("\n  --- After compact ---")
    print_records(sm.select_all(engine, "shop_db", "products"))

    # ───── UPDATE (FIXED) ─────
    rows = sm.select_all(engine, "shop_db", "products")
    target = rows[0]

    sm.update_record(
        engine,
        "shop_db",
        "products",
        target["_page"],
        target["_record_id"],
        {"price": "12.99"}
    )

    print("\n  --- After update ---")
    print_records(sm.select_all(engine, "shop_db", "products"))

    sm.show_table_stats(engine, "shop_db", "products")

    print("  [DEMO COMPLETE]\n")


# ─────────────────────────────────────────────
# TEST SUITE
# ─────────────────────────────────────────────
def run_test_suite(engine: dict):
    print("\n" + "=" * 60)
    print("  TEST SUITE (FIXED)")
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

    # ───── TEST 1 ─────
    def test_create():
        cleanup()
        assert sm.create_database(engine, "test_db")

        cols = [
            {"name": "id", "type": "INT", "size": 10},
            {"name": "name", "type": "VARCHAR", "size": 50},
            {"name": "age", "type": "INT", "size": 3},
        ]
        assert sm.create_table(engine, "test_db", "people", cols)

    # ───── TEST 2 ─────
    def test_insert():
        for r in [
            {"id": "1", "name": "Alice", "age": "30"},
            {"id": "2", "name": "Bob", "age": "40"},
            {"id": "3", "name": "Carol", "age": "28"},
        ]:
            sm.insert_record(engine, "test_db", "people", r)

        assert len(sm.select_all(engine, "test_db", "people")) == 3

    # ───── TEST 3 ─────
    def test_where():
        res = sm.select_where(engine, "test_db", "people", "age", "28")
        assert len(res) == 1
        assert res[0]["name"] == "Carol"

    # ───── TEST 4 ─────
    def test_delete():
        rows = sm.select_all(engine, "test_db", "people")
        bob = next(r for r in rows if r["name"] == "Bob")

        sm.delete_record(
            engine,
            "test_db",
            "people",
            bob["_page"],
            bob["_record_id"]
        )

        remaining = sm.select_all(engine, "test_db", "people")
        assert all(r["name"] != "Bob" for r in remaining)

    # ───── TEST 5 ─────
    def test_update():
        rows = sm.select_all(engine, "test_db", "people")
        alice = next(r for r in rows if r["id"] == "1")

        sm.update_record(
            engine,
            "test_db",
            "people",
            alice["_page"],
            alice["_record_id"],
            {"age": "31"}
        )

        updated = sm.select_where(engine, "test_db", "people", "id", "1")
        assert updated[0]["age"] == "31"

    # ───── TEST 6 ─────
    def test_compact():
        pages = sm._load_pages(engine, "test_db", "people")
        page_id = pages[0]["header"]["page_id"]

        removed = sm.compact_page(engine, "test_db", "people", page_id)
        assert removed >= 0

    # ───── TEST 7 ─────
    def test_stats():
        sm.show_table_stats(engine, "test_db", "people")

    # ───── TEST 8 ─────
    def test_cleanup():
        assert sm.drop_table(engine, "test_db", "people")
        assert sm.drop_database(engine, "test_db")

    for name, fn in [
        ("Create", test_create),
        ("Insert", test_insert),
        ("Where", test_where),
        ("Delete", test_delete),
        ("Update", test_update),
        ("Compact", test_compact),
        ("Stats", test_stats),
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
# INTERACTIVE CLI MENU
# ─────────────────────────────────────────────
def interactive_menu(engine: dict):
    current_db = None
    
    while True:
        print("\n" + "="*55)
        print(" Relational Database Storage Module Test (Stage 2)")
        print("=======================================================")
        print(f"Current active database: {current_db if current_db else '(None)'}")
        print("1. Set Active Database / Initialize Environment")
        print("2. Create Table")
        print("3. Insert Record")
        print("4. Select All Records")
        print("5. Update Record")
        print("6. Delete Record")
        print("7. Compact Page")
        print("8. Show Table Stats")
        print("9. Dump Page (Hex Dump & Slot Status)")
        print("10. Run Automated Test Suite")
        print("11. Exit")
        print("="*55)
        
        choice = input("Please select a function: ").strip()
        if choice == "1":
            db_name = input("Enter database name: ").strip()
            if not db_name:
                print("[ERROR] Database name cannot be empty.")
                continue
            sm.create_database(engine, db_name)
            current_db = db_name
            print(f"[OK] Active database set to '{current_db}'")
            
        elif choice == "2":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                print("[ERROR] Table name cannot be empty.")
                continue
            columns_str = input("Enter columns (format: name:type:size, e.g. pid:INT:10,name:VARCHAR:50): ").strip()
            if not columns_str:
                print("[ERROR] Columns cannot be empty.")
                continue
            try:
                columns = []
                for col in columns_str.split(","):
                    name, col_type, size = col.split(":")
                    columns.append({
                        "name": name.strip(),
                        "type": col_type.strip().upper(),
                        "size": int(size.strip())
                    })
                sm.create_table(engine, current_db, table_name, columns)
            except Exception as e:
                print(f"[ERROR] Invalid columns format: {e}")
                
        elif choice == "3":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            record_str = input("Enter record fields (format: key1=val1 key2=val2 ...): ").strip()
            try:
                fields = shlex.split(record_str)
                data = {}
                for f in fields:
                    if "=" in f:
                        k, v = f.split("=", 1)
                        print(k, v)
                        data[k.strip()] = v.strip()
                if not data:
                    print("[ERROR] No valid key=value fields provided.")
                    continue
                res = sm.insert_record(engine, current_db, table_name, data)
                if res:
                    print(f"[OK] Record inserted. Page ID: {res[0]}, Record ID: {res[1]}")
                else:
                    print("[ERROR] Failed to insert record.")
            except Exception as e:
                print(f"[ERROR] Failed to parse fields: {e}")
                
        elif choice == "4":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            try:
                records = sm.select_all(engine, current_db, table_name)
                print_records(records)
            except Exception as e:
                print(f"[ERROR] Failed to select records: {e}")
                
        elif choice == "5":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            try:
                page_id = int(input("Enter Page ID: ").strip())
                record_id = int(input("Enter Record ID: ").strip())
                record_str = input("Enter fields to update (format: key1=val1 key2=val2 ...): ").strip()
                fields = shlex.split(record_str)
                new_data = {}
                for f in fields:
                    if "=" in f:
                        k, v = f.split("=", 1)
                        new_data[k.strip()] = v.strip()
                if not new_data:
                    print("[ERROR] No fields to update.")
                    continue
                if sm.update_record(engine, current_db, table_name, page_id, record_id, new_data):
                    print("[OK] Record updated successfully.")
                else:
                    print("[ERROR] Failed to update record. Insufficient page space or record not found.")
            except Exception as e:
                print(f"[ERROR] Error: {e}")
                
        elif choice == "6":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            try:
                page_id = int(input("Enter Page ID: ").strip())
                record_id = int(input("Enter Record ID: ").strip())
                if sm.delete_record(engine, current_db, table_name, page_id, record_id):
                    print("[OK] Record deleted successfully.")
                else:
                    print("[ERROR] Record not found or already deleted.")
            except Exception as e:
                print(f"[ERROR] Error: {e}")
                
        elif choice == "7":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            try:
                page_id = int(input("Enter Page ID: ").strip())
                removed = sm.compact_page(engine, current_db, table_name, page_id)
                print(f"[OK] Page compacted. Removed {removed} deleted record(s).")
            except Exception as e:
                print(f"[ERROR] Error: {e}")
                
        elif choice == "8":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
            try:
                sm.show_table_stats(engine, current_db, table_name)
            except Exception as e:
                print(f"[ERROR] Error: {e}")
                
        elif choice == "9":
            if not current_db:
                print("[ERROR] Please set an active database first (Option 1).")
                continue
            table_name = input("Enter table name: ").strip()
            if not table_name:
                continue
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
                    else:
                        print("[ERROR] Page index out of range for hex dump.")
            except Exception as e:
                print(f"[ERROR] Error: {e}")
                
        elif choice == "10":
            print("\n[INFO] Running automated test suite...")
            run_test_suite(engine)
            
        elif choice == "11":
            print("[INFO] Exiting storage manager test CLI. Goodbye!")
            break
        else:
            print("[ERROR] Invalid choice. Please choose a number from 1 to 11.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="./MyTinyDB")
    parser.add_argument("command", nargs="?", default=None)

    args = parser.parse_args()
    engine = sm.init_storage(args.root)

    if args.command == "demo":
        run_demo(engine)
    elif args.command == "test":
        run_test_suite(engine)
    elif args.command is None:
        interactive_menu(engine)
    else:
        print("Unknown command. Use no arguments for interactive menu, or 'test' / 'demo'.")


if __name__ == "__main__":
    main()
