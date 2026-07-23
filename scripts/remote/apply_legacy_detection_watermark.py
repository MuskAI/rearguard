from pathlib import Path


TARGET = Path('/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/views/detection.py')
source = TARGET.read_text(encoding='utf-8')

if 'from concurrent.futures import ThreadPoolExecutor' not in source:
    import_marker = 'import base64\n'
    if import_marker not in source:
        raise SystemExit('import marker not found')
    source = source.replace(import_marker, import_marker + 'from concurrent.futures import ThreadPoolExecutor\n', 1)

marker = 'from .reasoning_view_formatter import format_frontend_reasoning, get_agent_reasoning_text\n'
helper = r'''
WATERMARK_PRECHECK_URL = os.environ.get(
    'REALGUARD_MODEL_VISIBLE_PRECHECK_URL',
    'http://127.0.0.1:5066/v1/precheck',
)
WATERMARK_PRECHECK_TOKEN = (
    os.environ.get('REALGUARD_MODEL_VISIBLE_PRECHECK_TOKEN')
    or os.environ.get('WATERMARK_PRECHECK_TOKEN')
    or ''
).strip()


def _run_explicit_watermark_precheck(image_path, filename):
    if not WATERMARK_PRECHECK_TOKEN:
        return {'status': 'unavailable', 'available': False, 'error': 'not_configured'}
    try:
        with open(image_path, 'rb') as image_file:
            response = requests.post(
                WATERMARK_PRECHECK_URL,
                headers={'Authorization': f'Bearer {WATERMARK_PRECHECK_TOKEN}'},
                files={'file': (filename or 'upload.bin', image_file, 'application/octet-stream')},
                timeout=(2, 25),
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError('invalid_watermark_precheck_response')
        return payload
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {
            'status': 'unavailable',
            'available': False,
            'error': type(exc).__name__,
            'explicitWatermark': {
                'detected': False,
                'type': 'none',
                'sourcePlatform': None,
                'confidence': 0.0,
                'confidenceBand': 'low',
                'hits': [],
            },
        }
'''
if 'def _run_explicit_watermark_precheck(' not in source:
    if marker not in source:
        raise SystemExit('detection import marker not found')
    source = source.replace(marker, marker + helper + '\n', 1)

executor_marker = ").strip()\n\n\ndef _run_explicit_watermark_precheck"
if '_WATERMARK_PRECHECK_EXECUTOR = ThreadPoolExecutor' not in source:
    if executor_marker not in source:
        raise SystemExit('watermark token marker not found')
    source = source.replace(
        executor_marker,
        ").strip()\n_WATERMARK_PRECHECK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='watermark-precheck')\n\n\ndef _run_explicit_watermark_precheck",
        1,
    )

old = '            agent_result = agent_detect(target_save_path)\n            explicit_watermark_precheck = _run_explicit_watermark_precheck(target_save_path, filename)\n'
new = '            explicit_watermark_future = _WATERMARK_PRECHECK_EXECUTOR.submit(_run_explicit_watermark_precheck, target_save_path, filename)\n            agent_result = agent_detect(target_save_path)\n            explicit_watermark_precheck = explicit_watermark_future.result()\n'
if old in source:
    source = source.replace(old, new, 1)
elif 'explicit_watermark_future = _WATERMARK_PRECHECK_EXECUTOR.submit' not in source:
    raise SystemExit('agent detection marker not found')

old = '                    "visual_issues": agent_result.get("visual_issues", ""),\n'
new = old + '                    "explicitWatermark": explicit_watermark_precheck.get("explicitWatermark") if isinstance(explicit_watermark_precheck, dict) else None,\n'
if '"explicitWatermark": explicit_watermark_precheck.get' not in source:
    if old not in source:
        raise SystemExit('response marker not found')
    source = source.replace(old, new, 1)

old = '                    "full_exif_info": all_exif_data\n'
new = '                    "watermarkPrecheck": explicit_watermark_precheck,\n' + old
if '"watermarkPrecheck": explicit_watermark_precheck' not in source:
    if old not in source:
        raise SystemExit('metadata response marker not found')
    source = source.replace(old, new, 1)

TARGET.write_text(source, encoding='utf-8')
print('legacy detection watermark integration applied')
