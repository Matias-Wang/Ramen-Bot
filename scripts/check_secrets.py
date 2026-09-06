"""commit 前的機密掃描，供 .git/hooks/pre-commit 呼叫。

檢查兩件事：
1. 檔名守門——任何 `.env` 檔案都不該進版控（2026-03-07 的外洩即因
   `.gitnore` 檔名拼錯導致 ignore 規則失效，`git add .` 把 `.env` 收了進去）。
2. 內容守門——僅掃描本次「新增的行」，比對高信心的機密樣式。

發現問題時以 exit code 1 阻止 commit，並以遮罩形式列出命中位置。
確認為誤判時可用 `git commit --no-verify` 略過。
"""

import re
import subprocess
import sys

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

# 檔名本身即為機密的樣式（.env.example 這類範本除外）
ENV_FILE_RE = re.compile(r"(^|/)\.env(\.|$)")
ENV_FILE_ALLOW_RE = re.compile(r"\.(example|sample|template|dist)$")

# 高信心機密樣式：命中即視為外洩
HIGH_CONFIDENCE = [
    ("Google API key", re.compile(r"AIzaSy[0-9A-Za-z_\-]{33}")),
    ("私鑰檔內容", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GCP 服務帳戶金鑰", re.compile(r'"type"\s*:\s*"service_account"')),
]

# 賦值型機密：KEY = <長字串>。需排除從環境變數取值與各種佔位符。
ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z_]*(?:token|secret|password|api[_-]?key)[A-Z_]*)\b"
    r"['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9+/_\-]{24,}={0,2})"
)
PLACEHOLDER_RE = re.compile(
    r"(?i)(getenv|environ|process\.env|secretkeyref|\$\{|<[a-z_]+>|"
    r"your[_-]|xxx+|placeholder|replace[_-]?me|example|dummy|redacted)"
)


def mask(value: str) -> str:
    """遮罩機密值，只留頭尾供辨識。"""
    if len(value) <= 12:
        return f"{value[:2]}…{value[-2:]} (len={len(value)})"
    return f"{value[:6]}…{value[-3:]} (len={len(value)})"


def staged_paths() -> list[str]:
    """取得本次 staged 的新增／修改檔案路徑。"""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
    ).stdout
    return [p for p in out.splitlines() if p.strip()]


def added_lines() -> list[tuple[str, str]]:
    """取得本次新增的行，回傳 (檔案路徑, 行內容) 串列。"""
    out = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--diff-filter=ACM"],
        capture_output=True, text=True, errors="replace",
    ).stdout
    results: list[tuple[str, str]] = []
    current = "?"
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            results.append((current, line[1:]))
    return results


def scan() -> list[str]:
    """執行掃描，回傳問題描述串列（空串列代表通過）。"""
    problems: list[str] = []

    for path in staged_paths():
        if ENV_FILE_RE.search(path) and not ENV_FILE_ALLOW_RE.search(path):
            problems.append(f"  [檔名] {path} —— .env 檔案不應進入版控")

    for path, line in added_lines():
        for label, pattern in HIGH_CONFIDENCE:
            m = pattern.search(line)
            if m:
                problems.append(f"  [{label}] {path}：{mask(m.group(0))}")

        m = ASSIGN_RE.search(line)
        if m and not PLACEHOLDER_RE.search(line):
            value = m.group(2)
            # 純數字或單一字元重複者視為非機密（版號、雜湊佔位等）
            if not value.isdigit() and len(set(value)) > 4:
                problems.append(
                    f"  [賦值型機密] {path}：{m.group(1)} = {mask(value)}"
                )

    return problems


def main() -> int:
    """掃描 staged 內容，發現機密時阻止 commit。"""
    print(f"{GREEN}[pre-commit] STEP 0: 機密掃描...{RESET}")
    try:
        problems = scan()
    except Exception as e:
        print(f"{RED}[pre-commit] STEP 0 ERROR:{e}{RESET}")
        return 1

    if not problems:
        print(f"{GREEN}[pre-commit] STEP 0: 機密掃描通過{RESET}")
        return 0

    print(f"{RED}[pre-commit] 偵測到疑似機密，已阻止 commit：{RESET}")
    for p in problems:
        print(f"{RED}{p}{RESET}")
    print(
        f"{YELLOW}\n機密應只存在於 .env（已被 .gitignore 排除）或 Secret Manager。\n"
        f"確認為誤判時可用：git commit --no-verify{RESET}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
