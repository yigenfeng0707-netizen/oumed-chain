"""软著源代码文档生成器（中国版权保护中心格式）

规则：每页 50 行；程序量不足 60 页全量提交，超过则提交前 30 页 + 后 30 页；
页眉含软件名称+版本号；剔除空行与纯注释行以提高密度。

运行：backend/.venv/Scripts/python scripts/build_copyright_src.py
输出：docs/软著申请/<软件名>-源代码.txt（三份）
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
OUT_DIR = ROOT / "docs" / "软著申请"
LINES_PER_PAGE = 50
PAGES = 30  # 前30页 + 后30页

TITLE_REGISTER = [
    {
        "file": "1-瓯医数链医疗数据要素可信流通平台V1.0-源代码.txt",
        "name": "瓯医数链医疗数据要素可信流通平台",
        "version": "V1.0",
        "roots": [BACKEND / "app"],
        "globs": ["*.py"],
        "exclude_substrings": ["test_", "scripts"],
    },
    {
        "file": "2-瓯医病历智能治理系统V1.0-源代码.txt",
        "name": "瓯医病历智能治理系统",
        "version": "V1.0",
        "roots": [BACKEND / "app" / "services", BACKEND / "app" / "routers",
                  ROOT / "frontend" / "src" / "app" / "governance", ROOT / "frontend" / "src" / "lib"],
        "globs": ["governance.py", "governance", "api.ts"],
        "exclude_substrings": [],
    },
    {
        "file": "3-瓯医联邦学习医疗协作引擎V1.0-源代码.txt",
        "name": "瓯医联邦学习医疗协作引擎",
        "version": "V1.0",
        "roots": [BACKEND / "app" / "services" / "federated", BACKEND / "app" / "routers",
                  ROOT / "frontend" / "src" / "app" / "federation"],
        "globs": ["*.py", "page.tsx"],
        "exclude_substrings": ["test_"],
        "filter_contains": True,  # 只取联邦相关文件
    },
]


def collect_files(item):
    files = []
    for root in item["roots"]:
        if root.is_file():
            files.append(root)
            continue
        if not root.exists():
            continue
        for g in item["globs"]:
            files.extend(root.rglob(g))
    seen, out = set(), []
    for f in files:
        rp = str(f.resolve())
        if rp in seen or f.name.startswith("__"):
            continue
        if any(x in str(f) for x in item.get("exclude_substrings", [])):
            continue
        if item.get("filter_contains") and not any(k in rp for k in ("federat", "governance", "federation")):
            continue
        if "api.ts" in f.name and item["file"].startswith("2") and "governance" not in rp:
            pass  # api.ts 允许进入治理系统材料
        seen.add(rp)
        out.append(f)
    return sorted(out, key=lambda p: str(p))


def code_lines(files):
    lines = []
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        kept = 0
        for ln in raw:
            t = ln.rstrip()
            if not t.strip():
                continue
            if t.strip().startswith("#") and kept > 0 and len(t.strip()) > 2 and not t.strip().startswith("#!"):
                # 保留有信息量的注释，剔除整行占位注释——版权中心要求高密度
                if len(t.strip()) < 6:
                    continue
            lines.append(t)
            kept += 1
        lines.append("")
    return lines[:6000] if len(lines) > 6000 else lines


def paginate(lines, soft_name, version):
    need = LINES_PER_PAGE * PAGES * 2
    if len(lines) <= need:
        selected = lines
    else:
        head = lines[: LINES_PER_PAGE * PAGES]
        tail = lines[-LINES_PER_PAGE * PAGES:]
        selected = head + ["…（中间部分略）…"] + tail

    out = []
    page = 1
    for i in range(0, len(selected), LINES_PER_PAGE):
        chunk = selected[i : i + LINES_PER_PAGE]
        out.append(f"第{page}页")
        out.append(f"{soft_name} {version}")
        out.extend(chunk)
        out.append("")
        page += 1
    return "\n".join(out), page - 1


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for item in TITLE_REGISTER:
        files = collect_files(item)
        lines = code_lines(files)
        content, pages = paginate(lines, item["name"], item["version"])
        out = OUT_DIR / item["file"]
        out.write_text(f"{item['name']} {item['version']}\n\n{content}", encoding="utf-8")
        print(f"✓ {item['file']}: {pages} 页，{len(lines)} 行代码，来源 {len(files)} 个文件")


if __name__ == "__main__":
    main()
