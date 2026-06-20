# Project 1

## 1. init_storage(root_dir: str) -> dict
- Load current `storage_config.json` at current dir
  - if config path exist -> `_read_json(config_path)`
  - else load `_default_config()` and `_write_json(config_path, config)`
- Load current `system_catalog.json` at current dir
  - if catalog_path not exist -> `_write_json(catalog_path, {"databases": {}})`
  
## 2. create_database(engine: dict, db_name: str) -> bool
- Load catalog and check exist
- Create dir with db name
- Add tables field into catalog and save 

## 3. create_table(engine: dict, db_name: str, table_name: str, columns: list[dict]) -> bool:
- Load catalog and check exist
- `tables[table_name] = {
        "columns":      columns,
        "record_count": 0,
        "page_count":   1,
        "created_at":   _now(),
    }`
- Save catalog

## 4. insert_record(engine: dict, db_name: str, table_name: str, data: dict) -> Optional[tuple[int, int]]:
- Load catalog, load config and check exist
- Load page
- check capability to add new record by checking remaning `free_space` and `MAX_SLOTS_PER_PAGE`