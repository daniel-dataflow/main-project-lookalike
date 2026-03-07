#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Tuple

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run search eval and write eval_table.csv")
    parser.add_argument("--cases", default="search_eval/cases/cases.csv")
    parser.add_argument("--output", default="search_eval/outputs/eval_table.csv")
    parser.add_argument("--api-url", default="http://localhost:8914/search")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to output CSV instead of overwriting.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Skip failed cases and continue.",
    )
    return parser.parse_args()


def read_cases(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"cases file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("cases.csv is empty")
    return rows


def build_request_payload(row: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, Tuple[str, bytes, str]]]:
    query_text = (row.get("query_text") or "").strip()
    query_type = (row.get("query_type") or "text").strip().lower()
    image_path = (row.get("query_image_path") or "").strip()

    data: Dict[str, str] = {}
    files: Dict[str, Tuple[str, bytes, str]] = {}

    allowed = {"text", "image", "image_text"}
    if query_type not in allowed:
        raise ValueError(
            f"invalid query_type={query_type} (query_id={row.get('query_id')}). "
            f"use one of: text, image, image_text"
        )

    if query_type in {"text", "image_text"}:
        if not query_text:
            raise ValueError(f"query_type={query_type} requires query_text (query_id={row.get('query_id')})")
        data["text"] = query_text

    if query_type in {"image", "image_text"}:
        if not image_path:
            raise ValueError(f"query_type={query_type} requires query_image_path (query_id={row.get('query_id')})")
        img_file = Path(image_path)
        if not img_file.exists():
            raise FileNotFoundError(f"image not found: {img_file}")
        files["image"] = (img_file.name, img_file.read_bytes(), "application/octet-stream")

    return data, files


def run_query(api_url: str, row: Dict[str, str], top_k: int, timeout: float) -> List[Dict[str, object]]:
    data, files = build_request_payload(row)
    resp = requests.post(api_url, data=data, files=files or None, timeout=timeout)
    if resp.status_code >= 400:
        qid = row.get("query_id", "")
        raise RuntimeError(f"HTTP {resp.status_code} for query_id={qid}: {resp.text.strip()}")
    payload = resp.json()
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("invalid response: results is not a list")
    return results[:top_k]


def extract_product_id(item: Dict[str, object]) -> str:
    # ML API: id, Backend API: product_id
    if item.get("id") is not None:
        return str(item.get("id"))
    return str(item.get("product_id", ""))


def extract_score(item: Dict[str, object]) -> object:
    # ML API: score, Backend API: similarity_score
    if item.get("score") is not None:
        return item.get("score")
    return item.get("similarity_score", "")


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    cases_path = Path(args.cases)
    output_path = Path(args.output)

    rows = read_cases(cases_path)
    ensure_parent_dir(output_path)

    fieldnames = [
        "query_id",
        "base_id",
        "query_type",
        "query_text",
        "query_image_path",
        "rank",
        "product_id",
        "score",
        "relevance",
    ]

    written = 0
    failed = 0
    file_exists = output_path.exists()
    write_mode = "a" if args.append else "w"

    with output_path.open(write_mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if (not args.append) or (args.append and not file_exists):
            writer.writeheader()

        for case in rows:
            query_id = (case.get("query_id") or "").strip()
            base_id = (case.get("base_id") or "").strip()
            query_type = (case.get("query_type") or "text").strip().lower()
            query_text = (case.get("query_text") or "").strip()
            query_image_path = (case.get("query_image_path") or "").strip()
            if not query_id:
                raise ValueError("query_id is required in cases.csv")

            try:
                results = run_query(args.api_url, case, args.top_k, args.timeout)
            except Exception as exc:
                failed += 1
                print(f"[ERROR] {exc}")
                if args.continue_on_error:
                    continue
                raise
            for idx, item in enumerate(results, start=1):
                writer.writerow(
                    {
                        "query_id": query_id,
                        "base_id": base_id,
                        "query_type": query_type,
                        "query_text": query_text,
                        "query_image_path": query_image_path,
                        "rank": idx,
                        "product_id": extract_product_id(item),
                        "score": extract_score(item),
                        "relevance": "",
                    }
                )
                written += 1

    print(f"done: wrote {written} rows, failed {failed} cases -> {output_path}")


if __name__ == "__main__":
    main()
