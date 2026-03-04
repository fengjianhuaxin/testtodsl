"""Global configuration."""
import json
import os

# ============ LLM ============
LLM_API_KEY = "sk-tdeaewbaihruwxzxyignlhqvoguifdqarsvjhsywrdpgmuoa"
LLM_BASE_URL = "https://api.siliconflow.cn/v1"
LLM_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"

# ============ Paths ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ONTOLOGY_DIR = os.path.join(BASE_DIR, "ontology")
MAPPING_DIR = os.path.join(BASE_DIR, "mapping")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
KNOWLEDGE_FILE = os.path.join(KNOWLEDGE_DIR, "domain_knowledge.json")
RUNTIME_DB_CONFIG_FILE = os.path.join(DATA_DIR, "db_config.json")
RUNTIME_LLM_CONFIG_FILE = os.path.join(DATA_DIR, "llm_config.json")
PROMPTS_FILE = os.path.join(DATA_DIR, "prompt_templates.json")

# ============ Store ============
# default: "sheet" | "mysql"
STORE_TYPE = "sheet"

# default mysql settings
MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "student_mgmt",
}

# reserved
NEO4J_CONFIG = {
    "uri": "bolt://localhost:7687",
    "user": "neo4j",
    "password": "",
}

# source registry
DATA_SOURCES = {
    "xksx": os.path.join(DATA_DIR, "xksx"),
}


def get_default_source_id() -> str:
    """Return the only/default source id for single-source mode."""
    if not DATA_SOURCES:
        return ""
    return next(iter(DATA_SOURCES.keys()))


def get_default_source_dir() -> str:
    source_id = get_default_source_id()
    if not source_id:
        return ""
    return DATA_SOURCES.get(source_id, "")


def load_runtime_db_config() -> dict:
    """Load runtime DB settings from `data/db_config.json` if present."""
    result = {
        "store_type": STORE_TYPE,
        "mysql": dict(MYSQL_CONFIG),
    }
    if not os.path.exists(RUNTIME_DB_CONFIG_FILE):
        return result

    try:
        with open(RUNTIME_DB_CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return result

    store_type = str(data.get("store_type", result["store_type"])).strip().lower()
    if store_type in ("sheet", "mysql"):
        result["store_type"] = store_type

    mysql_data = data.get("mysql", {})
    if isinstance(mysql_data, dict):
        merged = dict(result["mysql"])
        merged.update(mysql_data)
        try:
            merged["port"] = int(merged.get("port", 3306))
        except Exception:
            merged["port"] = 3306
        result["mysql"] = merged

    return result


def save_runtime_db_config(payload: dict):
    """Persist runtime DB settings to `data/db_config.json`."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RUNTIME_DB_CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def get_store_type() -> str:
    return load_runtime_db_config().get("store_type", STORE_TYPE)


def get_mysql_config() -> dict:
    return load_runtime_db_config().get("mysql", dict(MYSQL_CONFIG))


def load_runtime_llm_config() -> dict:
    """Load runtime LLM settings from `data/llm_config.json` if present."""
    result = {
        "api_key": LLM_API_KEY,
        "base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
    }

    if not os.path.exists(RUNTIME_LLM_CONFIG_FILE):
        return result

    try:
        with open(RUNTIME_LLM_CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return result

    if not isinstance(data, dict):
        return result

    api_key = str(data.get("api_key", "")).strip()
    base_url = str(data.get("base_url", "")).strip()
    model = str(data.get("model", "")).strip()

    if api_key:
        result["api_key"] = api_key
    if base_url:
        result["base_url"] = base_url
    if model:
        result["model"] = model
    return result


def save_runtime_llm_config(payload: dict):
    """Persist runtime LLM overrides to `data/llm_config.json`."""
    data = {}
    if isinstance(payload, dict):
        api_key = str(payload.get("api_key", "")).strip()
        base_url = str(payload.get("base_url", "")).strip()
        model = str(payload.get("model", "")).strip()
        if api_key:
            data["api_key"] = api_key
        if base_url:
            data["base_url"] = base_url
        if model:
            data["model"] = model

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RUNTIME_LLM_CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def get_runtime_llm_overrides() -> dict:
    """Get raw runtime LLM override values (without defaults)."""
    if not os.path.exists(RUNTIME_LLM_CONFIG_FILE):
        return {"api_key": "", "base_url": "", "model": ""}
    try:
        with open(RUNTIME_LLM_CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {"api_key": "", "base_url": "", "model": ""}
    except Exception:
        return {"api_key": "", "base_url": "", "model": ""}

    return {
        "api_key": str(data.get("api_key", "")).strip(),
        "base_url": str(data.get("base_url", "")).strip(),
        "model": str(data.get("model", "")).strip(),
    }
