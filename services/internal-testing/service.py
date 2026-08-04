"""Loopback-only internal evaluation service hosted beside the GPU models."""
from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import os
import shutil
from pathlib import Path
from urllib.parse import unquote

import requests
from flask import Flask, Response, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


def _decode_model(value: str, url_map: dict[str, str]) -> dict:
    if not value:
        raise ValueError("缺少由公网管理端签发的模型配置")
    if len(value) > 65536:
        raise ValueError("模型配置过大")
    try:
        padding = "=" * (-len(value) % 4)
        model = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("模型配置无效") from exc
    if not isinstance(model, dict) or not model.get("id"):
        raise ValueError("模型配置无效")
    model = dict(model)
    for key in ("endpoint", "healthUrl"):
        current = str(model.get(key) or "").strip()
        for source, target in sorted(url_map.items(), key=lambda item: len(item[0]), reverse=True):
            if current == source or current.startswith(f"{source}/"):
                current = f"{target}{current[len(source):]}"
                break
        model[key] = current
    endpoint = str(model.get("endpoint") or "")
    if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("内部评测模型必须映射到 66 服务器的本机服务")
    return model


def _json_error(message: str, status: int):
    return jsonify({"status": "error", "message": str(message)}), status


def create_app(core=None, *, service_token: str | None = None) -> Flask:
    if core is None:
        from imagedetection.views import internal_testing as core

    app = Flask(__name__)
    token = str(service_token or os.environ.get("REALGUARD_INTERNAL_TESTING_TOKEN") or "").strip()
    try:
        configured_map = json.loads(
            os.environ.get("REALGUARD_INTERNAL_TEST_MODEL_URL_MAP", "{}") or "{}"
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("REALGUARD_INTERNAL_TEST_MODEL_URL_MAP must be valid JSON") from exc
    url_map = {
        str(source).rstrip("/"): str(target).rstrip("/")
        for source, target in configured_map.items()
        if str(source).strip() and str(target).strip()
    }

    @app.before_request
    def authenticate():
        if not token:
            return _json_error("内部评测服务令牌未配置", 503)
        supplied = str(request.headers.get("Authorization") or "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(supplied, expected):
            return _json_error("内部评测服务鉴权失败", 401)
        return None

    @app.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(ValueError)
    def invalid_request(exc):
        message = str(exc)
        status = 507 if "磁盘" in message and "不足" in message else 400
        return _json_error(message, status)

    @app.errorhandler(requests.RequestException)
    def upstream_error(exc):
        return _json_error(str(exc), 502)

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        if isinstance(exc, HTTPException):
            return _json_error(exc.description, int(exc.code or 500))
        app.logger.exception("internal testing request failed")
        return _json_error("内部评测服务处理失败，请稍后重试", 500)

    def actor() -> dict:
        raw_id = str(request.headers.get("X-RealGuard-Actor-Id") or "").strip()
        try:
            admin_id = int(raw_id) if raw_id else None
        except ValueError:
            admin_id = None
        return {
            "adminId": admin_id,
            "Userid": raw_id if admin_id is None else "",
            "username": str(request.headers.get("X-RealGuard-Actor-Name") or "")[:128],
            "authType": "admin_account",
        }

    def model() -> dict:
        return _decode_model(
            str(request.headers.get("X-RealGuard-Testing-Model") or ""),
            url_map,
        )

    def owned_import(import_id: str):
        item = core.get_import_session(import_id, actor=actor())
        if item:
            return item, None
        return None, _json_error("数据集上传会话不存在", 404)

    @app.get("/internal/testing/health")
    def health():
        core.ensure_schema()
        usage = shutil.disk_usage(core.DATA_ROOT)
        return jsonify({
            "status": "success",
            "service": "realguard-internal-testing",
            "storage": {
                "host": os.environ.get("REALGUARD_INTERNAL_TEST_STORAGE_HOST", "10.1.20.66"),
                "totalBytes": usage.total,
                "usedBytes": usage.used,
                "freeBytes": usage.free,
            },
        })

    @app.get("/api/admin/testing/overview")
    def overview():
        payload = core.overview(actor=actor())
        usage = shutil.disk_usage(core.DATA_ROOT)
        payload["storage"] = {
            "host": os.environ.get("REALGUARD_INTERNAL_TEST_STORAGE_HOST", "10.1.20.66"),
            "role": "algorithm_server",
            "totalBytes": usage.total,
            "usedBytes": usage.used,
            "freeBytes": usage.free,
        }
        return jsonify({"status": "success", **payload})

    @app.post("/api/admin/testing/datasets")
    def create_dataset():
        content_length = max(0, int(request.content_length or 0))
        if content_length and content_length > core.available_storage_bytes():
            return _json_error("服务器磁盘剩余空间不足，无法接收该数据集", 507)
        uploads = [
            (uploaded.filename, uploaded.stream)
            for uploaded in request.files.getlist("files")
            if uploaded and uploaded.filename
        ]
        raw_labels = str(request.form.get("labels") or "").strip()
        labels = json.loads(raw_labels) if raw_labels else {}
        if not isinstance(labels, dict):
            raise ValueError("标签映射必须是 JSON 对象")
        dataset = core.create_dataset(
            uploads,
            source_url=request.form.get("sourceUrl") or "",
            name=request.form.get("name") or "",
            default_label=request.form.get("defaultLabel") or "unlabeled",
            labels=labels,
            actor=actor(),
        )
        return jsonify({"status": "success", "dataset": dataset}), 201

    @app.post("/api/admin/testing/dataset-imports")
    def create_import():
        payload = request.get_json(silent=True) or {}
        stream_evaluation = bool(payload.get("streamEvaluation"))
        item = core.create_import_session(
            name=payload.get("name") or "",
            default_label=payload.get("defaultLabel") or "unlabeled",
            source_url=payload.get("sourceUrl") or "",
            expected_files=payload.get("expectedFiles") or 0,
            expected_bytes=payload.get("expectedBytes") or 0,
            stream_evaluation=stream_evaluation,
            model=model() if stream_evaluation else None,
            concurrency=payload.get("concurrency") or 1,
            actor=actor(),
        )
        return jsonify({"status": "success", "importSession": item}), 201

    @app.route("/api/admin/testing/dataset-imports/<import_id>", methods=["GET", "DELETE"])
    def import_session(import_id):
        item, error = owned_import(import_id)
        if error:
            return error
        if request.method == "GET":
            return jsonify({"status": "success", "importSession": item})
        deleted = core.delete_import_session(import_id)
        if not deleted:
            return _json_error("数据集上传会话不存在", 404)
        return jsonify({"status": "success", "deleted": import_id})

    @app.post("/api/admin/testing/dataset-imports/<import_id>/files")
    def import_files(import_id):
        _, error = owned_import(import_id)
        if error:
            return error
        if int(request.content_length or 0) > core.available_storage_bytes():
            return _json_error("服务器磁盘剩余空间不足", 507)
        uploads = [
            (uploaded.filename, uploaded.stream)
            for uploaded in request.files.getlist("files")
            if uploaded and uploaded.filename
        ]
        if not uploads:
            return jsonify({
                "status": "error",
                "code": "empty_upload_batch",
                "message": "浏览器未提交本批次文件，系统将重建请求后重试",
            }), 422
        item = core.add_import_files(import_id, uploads)
        return jsonify({"status": "success", "importSession": item})

    @app.post("/api/admin/testing/dataset-imports/<import_id>/chunks")
    def import_chunk(import_id):
        _, error = owned_import(import_id)
        if error:
            return error
        is_raw = request.mimetype == "application/octet-stream"
        uploaded = None if is_raw else request.files.get("chunk")
        chunk = request.stream if is_raw else (uploaded.stream if uploaded else None)
        if chunk is None:
            raise ValueError("缺少文件分块")
        values = request.headers if is_raw else request.form
        prefix = "X-Upload-" if is_raw else ""
        relative_path = values.get(f"{prefix}Relative-Path") or ""
        if is_raw:
            relative_path = unquote(relative_path)
        expected_name = f"{prefix}Expected-Bytes" if is_raw else "expectedBytes"
        expected_raw = values.get(expected_name)
        item = core.add_import_chunk(
            import_id,
            upload_id=values.get(f"{prefix}Id") or "",
            relative_path=relative_path or (uploaded.filename if uploaded else "archive.zip"),
            chunk_index=int(values.get(f"{prefix}Chunk-Index" if is_raw else "chunkIndex") or 0),
            total_chunks=int(values.get(f"{prefix}Total-Chunks" if is_raw else "totalChunks") or 0),
            expected_bytes=int(expected_raw) if expected_raw not in (None, "") else None,
            chunk=chunk,
        )
        return jsonify({"status": "success", "importSession": item})

    @app.post("/api/admin/testing/dataset-imports/<import_id>/resume")
    def resume_import(import_id):
        _, error = owned_import(import_id)
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        files = payload.get("files") or []
        if not isinstance(files, list):
            raise ValueError("文件清单格式无效")
        state = core.get_import_resume_state(import_id, files)
        return jsonify({"status": "success", "resumeState": state})

    @app.post("/api/admin/testing/dataset-imports/<import_id>/finalize")
    def finalize_import(import_id):
        _, error = owned_import(import_id)
        if error:
            return error
        item = core.finalize_import(import_id)
        return jsonify({"status": "success", "importSession": item}), 202

    @app.route("/api/admin/testing/datasets/<dataset_id>", methods=["GET", "DELETE"])
    def dataset(dataset_id):
        if request.method == "GET":
            item = core.get_dataset(dataset_id, include_samples=True)
            if not item:
                return _json_error("测试数据集不存在", 404)
            return jsonify({"status": "success", "dataset": item})
        deleted = core.delete_dataset(dataset_id)
        if not deleted:
            return _json_error("测试数据集不存在", 404)
        return jsonify({"status": "success", "deleted": dataset_id})

    @app.get("/api/admin/testing/samples/<sample_id>/image")
    def sample_image(sample_id):
        item = core.sample_path(sample_id)
        if not item:
            return _json_error("测试样本不存在", 404)
        path, mime_type, name = item
        return send_file(path, mimetype=mime_type, download_name=name, conditional=True, max_age=0)

    @app.route("/api/admin/testing/samples/<sample_id>", methods=["PATCH", "POST"])
    def update_sample(sample_id):
        payload = request.get_json(silent=True) or {}
        label = str(payload.get("groundTruth") or "").strip().lower()
        if label not in core.ALLOWED_LABELS:
            raise ValueError("标签必须为 real、fake 或 unlabeled")
        item = core.update_sample_label(sample_id, label)
        if not item:
            return _json_error("测试样本不存在", 404)
        return jsonify({"status": "success", "sample": item})

    @app.post("/api/admin/testing/runs")
    def create_run():
        payload = request.get_json(silent=True) or {}
        run = core.create_evaluation(
            str(payload.get("datasetId") or ""),
            model(),
            concurrency=payload.get("concurrency") or 1,
            actor=actor(),
        )
        return jsonify({"status": "success", "run": run}), 202

    @app.post("/api/admin/testing/load-tests")
    def create_load_test():
        if request.form.get("confirmation") != "INTERNAL_LOAD_TEST":
            raise ValueError("请确认这是受控内部压测")
        active = [
            run for run in core.list_runs(100)
            if run.get("kind") == "load_test"
            and run.get("status") in {"queued", "running", "cancel_requested"}
        ]
        if active:
            return _json_error("已有压测任务运行中，请等待或先停止", 409)
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            raise ValueError("压测必须上传固定测试图片")
        image = uploaded.stream.read(core.MAX_UPLOAD_BYTES + 1)
        if len(image) > core.MAX_UPLOAD_BYTES:
            return _json_error("压测图片超过 24 MB", 413)
        run = core.create_load_test(
            model(),
            image,
            uploaded.filename,
            uploaded.mimetype or "application/octet-stream",
            concurrency=request.form.get("concurrency") or 1,
            request_count=request.form.get("requestCount") or 20,
            duration_seconds=request.form.get("durationSeconds") or 30,
            actor=actor(),
        )
        return jsonify({"status": "success", "run": run}), 202

    @app.get("/api/admin/testing/runs/<run_id>")
    def run(run_id):
        item = core.get_run(run_id, include_results=True, result_limit=200)
        if not item:
            return _json_error("测试任务不存在", 404)
        return jsonify({"status": "success", "run": item})

    @app.post("/api/admin/testing/runs/<run_id>/cancel")
    def cancel_run(run_id):
        item = core.cancel_run(run_id)
        if not item:
            return _json_error("测试任务不存在", 404)
        return jsonify({"status": "success", "run": item})

    @app.get("/api/admin/testing/runs/<run_id>/export")
    def export_run(run_id):
        item = core.get_run(run_id, include_results=True, result_limit=None)
        if not item:
            return _json_error("测试任务不存在", 404)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "sample_id", "sample_name", "relative_path", "class_path", "group_id",
            "subclasses", "ground_truth", "predicted_label", "score", "status",
            "latency_ms", "http_status", "error",
        ])
        for result in item.get("results") or []:
            writer.writerow([
                result.get("sample_id"), result.get("sample_name"), result.get("relative_path"),
                result.get("class_path"), result.get("group_id"),
                json.dumps(result.get("subclasses") or {}, ensure_ascii=False),
                result.get("ground_truth"), result.get("predicted_label"), result.get("score"),
                result.get("status"), result.get("latency_ms"), result.get("http_status"),
                result.get("error"),
            ])
        response = Response(output.getvalue(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="{run_id}.csv"'
        return response

    return app


app = create_app()
