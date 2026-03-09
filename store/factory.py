"""Data store factory."""
import config
from store.sheet_store import SheetDataStore


def create_data_store(mapping_manager):
    store_type = str(config.get_store_type()).strip().lower()
    if store_type == "mysql":
        from store.mysql_store import MySQLDataStore
        return MySQLDataStore(config.get_mysql_config(), mapping_manager)
    return SheetDataStore(config.DATA_SOURCES, mapping_manager)
