"""任务资料体检、完整Issue审阅与无模型的容器红绿验证。"""

import difflib
import io
import keyword
import re
import tokenize
import uuid
from collections import Counter
from pathlib import Path

from patchpilot.evaluation.catalog import acceptance_directory, load_dataset
from patchpilot.evaluation.runner import write_json
from patchpilot.sandbox import DEFAULT_IMAGE, DockerRunner
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task
from patchpilot.verification import verify_patch
from patchpilot.workspace import create_workspace


def _normalized_tokens(source: str) -> list[str]:
    result = []
    ignored = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in ignored:
            continue
        value = token.string
        if token.type == tokenize.NAME and not keyword.iskeyword(value):
            value = "NAME"
        elif token.type in (tokenize.STRING, tokenize.NUMBER):
            value = "LITERAL"
        result.append(value)
    return result


def audit_dataset(tasks_root: Path, solutions_root: Path) -> dict:
    dataset = load_dataset()
    sources = {}
    errors = []
    for item in dataset.tasks:
        try:
            task = load_task(tasks_root / item.task_id / "task.json")
            if (
                not (solutions_root / item.task_id / "reference.patch").is_file()
                or not acceptance_directory(item.task_id).is_dir()
            ):
                raise ValueError("缺少维护者材料")
            if any(
                "acceptance" in path.name or path.name == "reference.patch"
                for path in task.snapshot_root.rglob("*")
            ):
                raise ValueError("公开快照混入答案")
            source = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted((task.snapshot_root / "src").rglob("*.py"))
            )
            sources[item.task_id] = _normalized_tokens(source)
        except (ValueError, OSError, tokenize.TokenError):
            errors.append({"task_id": item.task_id, "reason": "invalid_task_material"})
    similar = []
    names = sorted(sources)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            ratio = difflib.SequenceMatcher(
                None, sources[left], sources[right], autojunk=False
            ).ratio()
            if ratio >= 0.9:
                similar.append(
                    {"left": left, "right": right, "normalized_token_similarity": round(ratio, 4)}
                )
    return {
        "dataset_version": dataset.version,
        "dataset_sha256": dataset.fingerprint(tasks_root, solutions_root),
        "task_count": len(dataset.tasks),
        "splits": dict(Counter(item.split for item in dataset.tasks)),
        "groups": len({item.group for item in dataset.tasks}),
        "categories": dict(Counter(item.category for item in dataset.tasks)),
        "material_errors": errors,
        "near_duplicate_candidates": similar,
        "review_status": dataset.review_status,
        "limitations": [
            "token相似度是近重复候选提示，不是语义重复率",
            "合成小型缺陷不代表生产分布，可能让强模型接近饱和",
            "保留集构建者已知，不能保证训练无污染",
        ],
    }


def write_review(path: Path, tasks_root: Path) -> None:
    dataset = load_dataset()
    lines = [
        "# 候选基准输入与评分规则审阅",
        "",
        dataset.provenance,
        "",
        "资料许可：benchmarks/LICENSE（MIT，仅基准资料）。当前是待人工审阅候选，不假定已获用户批准。",
        "",
        "开发12项、保留8项，关联group不跨拆分；保留不等于盲测。",
        "",
        "## 评分规则",
        "",
        "- 检查候选最终行为，不要求补丁文字等同参考答案。",
        "- 固定base的新工作区应用封存补丁，在新容器运行原测试和维护者验收。",
        "- 原测试/配置/依赖不可改；无实际测试、空补丁和异常不能冒充通过。",
        "- 环境/服务失败保留在主分母，条件能力指标另列。",
        "- 测试不是正确性证明，仍需人工审查特判与设计质量。",
        "",
        "| ID | 拆分 | group | 预期行为 |",
        "| --- | --- | --- | --- |",
    ]
    for item in dataset.tasks:
        lines.append(f"| {item.task_id} | {item.split} | {item.group} | {item.expected_behavior} |")
    for item in dataset.tasks:
        issue = (tasks_root / item.task_id / "Issue.md").read_text(encoding="utf-8")
        length = max(
            4, max((len(match.group()) + 1 for match in re.finditer(r"`+", issue)), default=4)
        )
        fence = "`" * length
        lines.extend(
            [
                "",
                f"## {item.task_id}",
                "",
                f"来源：{item.source_kind}；许可：{item.license}；拆分：{item.split}",
                "",
                fence + "text",
                issue.rstrip(),
                fence,
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_tasks(
    tasks_root: Path,
    solutions_root: Path,
    output_root: Path,
    *,
    task_ids: list[str] | None = None,
    runner_factory=None,
) -> dict:
    dataset = load_dataset()
    selected = dataset.select(split="all", task_ids=task_ids)
    factory = runner_factory or (lambda image: DockerRunner(image=image))
    root = output_root.resolve() / f"v-{uuid.uuid4().hex[:12]}"
    root.mkdir(parents=True)
    image = None
    try:
        image = factory(DEFAULT_IMAGE).check()["image_id"]
    except Exception:
        pass
    rows = []
    for index, item in enumerate(selected):
        row = {
            "task_id": item.task_id,
            "valid": False,
            "reason": "environment_failed",
            "image_id": image,
        }
        try:
            if image is None:
                continue
            task = load_task(tasks_root / item.task_id / "task.json")
            case = root / f"c{index:03d}"
            runner = factory(image)
            normal = runner.run(create_workspace(task, case / "n"), targets=("tests",))
            repro = runner.run(create_workspace(task, case / "r"), targets=("repro",))
            row.update(
                normal_status=normal.status,
                repro_status=repro.status,
                snapshot_sha256=task.spec.snapshot_sha256,
            )
            if (
                normal.status != "passed"
                or repro.status != "failed"
                or not repro.failed
                or repro.errors
            ):
                row["reason"] = "not_reproduced_or_invalid_tests"
                continue
            patch = (solutions_root / item.task_id / "reference.patch").read_bytes()
            verdict = verify_patch(task, patch, case / "g", runner)
            row.update(reference_status=verdict.status, acceptance_sha256=verdict.acceptance_sha256)
            if (
                verdict.status != "passed"
                or compute_snapshot_hash(task.snapshot_root) != task.spec.snapshot_sha256
            ):
                row["reason"] = "reference_failed"
            else:
                row.update(valid=True, reason="validated")
        except Exception:
            row["reason"] = "invalid_task_or_environment"
        finally:
            rows.append(row)
            write_json(root / f"case-{index:03d}.json", row)
    report = {
        "result_kind": "dataset-validation-not-model-performance",
        "total": len(rows),
        "valid": sum(row["valid"] for row in rows),
        "records": rows,
        "output_path": str(root),
        "dataset_sha256": dataset.fingerprint(tasks_root, solutions_root),
    }
    write_json(root / "validation.json", report)
    return report
