"""
Centralized constants for Bug Analysis Workflow.

All hardcoded thresholds, weights, keywords, and limits are defined here
to avoid scattering magic values across implementation files.
"""

# ── Confidence thresholds ──
CONFIDENCE_HIGH_THRESHOLD = 0.7
CONFIDENCE_LOW_THRESHOLD = 0.5
CONFIDENCE_TOP_K_SEARCH = 5

# ── Routing confidence ──
KEYWORD_ROUTE_BASE_CONFIDENCE = 0.5
KEYWORD_ROUTE_PER_KEYWORD_BONUS = 0.15
KEYWORD_ROUTE_MAX_CONFIDENCE = 0.85
TRACE_ROUTE_CONFIDENCE = 0.95
KB_ROUTE_CONFIDENCE = 0.85
ROUTE_MERGE_BONUS = 0.1
RELATED_REPO_WEIGHT = 0.5
MAX_RELATED_REPOS = 5
TRACE_ROUTE_THRESHOLD = 0.9

# ── Cross-service ──
CROSS_SERVICE_CONFIDENCE = 0.5

# ── Go standard library (adapter) ──
GO_STDLIB = [
    "fmt", "log", "strings", "strconv", "json", "time",
    "context", "errors", "sync", "io", "os", "net", "http",
    "database", "sql", "regexp", "math", "rand", "crypto",
]

# ── File skip patterns (adapters/base.py) ──
FILE_SKIP_PATTERNS = [
    "test", "Test", "_test", "spec", "Spec",
    "config", "Config", "conf",
    "vendor", "node_modules", "target", "build",
]

# ── Git / changed files ──
CHANGED_FILES_BASE_BRANCHES = ["origin/master", "origin/main", "master", "main"]
CHANGED_FILES_TIMEOUT = 10
CHANGED_FILES_MAX_HINTS = 10
CHANGED_FILES_EXCLUDED_MODULES = {"main", "init", "common", "util", "utils", "base"}
CHANGED_FILE_EXTENSIONS = (".go", ".java", ".ts", ".tsx")

# ── AI Analysis ──
AI_MAX_TOKENS = 4096
AI_CONFIDENCE_BOOST_VERIFIED = 0.1
AI_CONFIDENCE_PENALTY_NO_LOCATIONS = 0.2
AI_CONFIDENCE_MIN_FOR_SAVE = 0.7
AI_PROMPT_TEMPLATE_DIR = "prompts"
CASE_RESULT_CONFIDENCE = 0.95
