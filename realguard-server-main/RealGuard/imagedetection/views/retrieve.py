import os
import json
import uuid

from flask import Blueprint, render_template, request, session, jsonify, send_file

from imagedetection.views.utils import excute_sql, get_now_str, get_file_size_str

retrieve_blueprint = Blueprint('retrieve_blueprint', __name__, static_folder='static')

current_dir = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
# 检索 API 配置
# ==============================================================================
LOCAL_IMAGE_LIBRARY_ROOT = os.environ.get(
    'LOCAL_IMAGE_LIBRARY_ROOT',
    '/media/disk4/dmm_data/retrieve_output/output/image'
).strip()
LOCAL_VIDEO_LIBRARY_ROOT = os.environ.get(
    'LOCAL_VIDEO_LIBRARY_ROOT',
    '/media/disk4/dmm_data/retrieve_output/output/video'
).strip()

ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv'}


def _list_local_libraries(root_path):
    """从本机目录扫描检索库（仅一级子目录）。"""
    root = (root_path or '').strip()
    if not root or not os.path.isdir(root):
        return []
    libs = []
    try:
        for name in os.listdir(root):
            full_path = os.path.join(root, name)
            if os.path.isdir(full_path):
                libs.append(name.strip())
    except OSError:
        return []
    return sorted(set([x for x in libs if x]))


def list_retrieve_libraries(search_type):
    """
    获取检索库列表：仅扫描本机目录（图像/视频分开）。
    """
    local_root = LOCAL_IMAGE_LIBRARY_ROOT if search_type == 'image' else LOCAL_VIDEO_LIBRARY_ROOT
    return _list_local_libraries(local_root)


def _normalize_rel_path(raw_path, root_path):
    """把绝对路径/相对路径标准化为相对库根目录路径。"""
    p = str(raw_path or '').strip()
    if not p:
        return ''
    p = p.replace('\\', '/')
    root_abs = os.path.abspath(root_path)
    if os.path.isabs(p):
        abs_p = os.path.abspath(p)
        if abs_p.startswith(root_abs + os.sep):
            rel = os.path.relpath(abs_p, root_abs)
            return rel.replace('\\', '/')
        return ''
    return p.lstrip('/')


def _normalize_results_paths(results, search_type):
    """将检索结果中的路径统一为本机库相对路径，供前端拼接本机文件代理 URL。"""
    root_path = LOCAL_IMAGE_LIBRARY_ROOT if search_type == 'image' else LOCAL_VIDEO_LIBRARY_ROOT
    normalized = []
    for item in (results or []):
        if not isinstance(item, dict):
            continue
        cur = dict(item)
        product = dict(cur.get('product') or {})

        rel_from_id = _normalize_rel_path(cur.get('id', ''), root_path)
        rel_from_product = _normalize_rel_path(product.get('product_images', ''), root_path)
        rel_path = rel_from_id or rel_from_product

        if rel_path:
            cur['id'] = rel_path
            product['product_images'] = rel_path
            cur['product'] = product
        normalized.append(cur)
    return normalized


def _normalize_similarity_scores(results):
    """将检索相似度归一化到 0~1。"""
    vals = []
    for item in (results or []):
        try:
            vals.append(float(item.get('score', 0.0)))
        except Exception:
            vals.append(0.0)
    if not vals:
        return results

    lo = min(vals)
    hi = max(vals)
    in_zero_one = (lo >= 0.0 and hi <= 1.0)
    in_minus_one_one = (lo >= -1.0 and hi <= 1.0)

    for item in results:
        try:
            raw = float(item.get('score', 0.0))
        except Exception:
            raw = 0.0
        if in_zero_one:
            norm = raw
        elif in_minus_one_one:
            norm = (raw + 1.0) / 2.0
        elif hi > lo:
            norm = (raw - lo) / (hi - lo)
        else:
            norm = 1.0
        if norm < 0.0:
            norm = 0.0
        elif norm > 1.0:
            norm = 1.0
        item['score_raw'] = raw
        item['score'] = round(norm, 6)
    return results


def _library_root_by_type(search_type):
    return LOCAL_IMAGE_LIBRARY_ROOT if search_type == 'image' else LOCAL_VIDEO_LIBRARY_ROOT


def _resolve_existing_file(root_path, rel_norm, search_type):
    """
    兼容不同库目录约定（如 ImageData vs imageData/ImageData）。
    返回命中的绝对路径，未命中返回空字符串。
    """
    candidates = [rel_norm]
    if search_type == 'image':
        if '/ImageData/' in rel_norm:
            candidates.append(rel_norm.replace('/ImageData/', '/imageData/ImageData/', 1))
            candidates.append(rel_norm.replace('/ImageData/', '/imageData/', 1))
    else:
        if '/VideoData/' in rel_norm:
            candidates.append(rel_norm.replace('/VideoData/', '/videoData/VideoData/', 1))
            candidates.append(rel_norm.replace('/VideoData/', '/videoData/', 1))

    # 通用兜底：将第2级目录首字母小写（如 flicker8k/ImageData -> flicker8k/imageData）
    parts = rel_norm.split('/')
    if len(parts) >= 3 and parts[1]:
        lowered = parts[:]
        lowered[1] = lowered[1][0].lower() + lowered[1][1:]
        candidates.append('/'.join(lowered))
        if search_type == 'image' and lowered[1] == 'imageData' and parts[1] == 'ImageData':
            candidates.append('/'.join([parts[0], 'imageData', 'ImageData'] + parts[2:]))

    root_abs = os.path.abspath(root_path)
    for rel in candidates:
        rel_try = os.path.normpath(rel).lstrip(os.sep)
        if rel_try.startswith('..'):
            continue
        target = os.path.abspath(os.path.join(root_abs, rel_try))
        if not (target == root_abs or target.startswith(root_abs + os.sep)):
            continue
        if os.path.isfile(target):
            return target
    return ''


def _build_local_retrieve_results(search_type, dataset, query_file_path, top_k):
    from imagedetection.views.local_retrieval_engine import get_engine

    root_path = _library_root_by_type(search_type)
    engine = get_engine(search_type=search_type, dataset=dataset, root_path=root_path)
    return engine.search(query_path=query_file_path, top_k=top_k)


@retrieve_blueprint.route('/retrieve/library-file/<search_type>/<path:rel_path>')
def retrieve_library_file(search_type, rel_path):
    """从本机检索库读取图片/视频文件给前端展示。"""
    if search_type not in ('image', 'video'):
        return jsonify({'status': 'error', 'message': '无效的检索类型'}), 400

    root_path = _library_root_by_type(search_type)
    if not os.path.isdir(root_path):
        return jsonify({'status': 'error', 'message': f'检索库目录不存在: {root_path}'}), 404

    rel_norm = os.path.normpath(rel_path).lstrip(os.sep)
    if rel_norm.startswith('..'):
        return jsonify({'status': 'error', 'message': '非法路径'}), 400

    target = _resolve_existing_file(root_path, rel_norm, search_type)
    if not target:
        return jsonify({'status': 'error', 'message': '文件不存在'}), 404
    return send_file(target, conditional=True)


def get_file_type(filename):
    if not filename or '.' not in filename:
        return 'unknown'
    ext = filename.rsplit('.', 1)[1].lower()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return 'image'
    elif ext in ALLOWED_VIDEO_EXTENSIONS:
        return 'video'
    return 'unknown'


# ==============================================================================
# 页面路由
# ==============================================================================

@retrieve_blueprint.route('/retrieve')
def retrieve_page():
    """兼容旧路由，默认跳转图像检索"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('image_retrieve.html')


@retrieve_blueprint.route('/image_retrieve')
def image_retrieve_page():
    """图像检索页面"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('image_retrieve.html')


@retrieve_blueprint.route('/video_retrieve')
def video_retrieve_page():
    """视频检索页面"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('video_retrieve.html')


@retrieve_blueprint.route('/retrieve/libraries', methods=['GET'])
def retrieve_libraries():
    """返回可切换的检索库列表及当前选择。"""
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    search_type = request.args.get('search_type', 'image')
    if search_type not in ('image', 'video'):
        return jsonify({'status': 'error', 'message': 'search_type 必须为 image 或 video'}), 400

    libraries = list_retrieve_libraries(search_type)
    selected_key = 'image_retrieve_library' if search_type == 'image' else 'video_retrieve_library'
    selected = session.get(selected_key)
    if selected not in libraries:
        selected = libraries[0] if libraries else ''

    return jsonify({
        'status': 'success',
        'search_type': search_type,
        'root_path': LOCAL_IMAGE_LIBRARY_ROOT if search_type == 'image' else LOCAL_VIDEO_LIBRARY_ROOT,
        'libraries': libraries,
        'selected': selected,
    })


@retrieve_blueprint.route('/retrieve/library/select', methods=['POST'])
def select_retrieve_library():
    """保存当前用户选择的图像/视频检索库。"""
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    payload = request.get_json(silent=True) or request.form
    search_type = (payload.get('search_type') or '').strip()
    library = (payload.get('library') or '').strip()
    if search_type not in ('image', 'video'):
        return jsonify({'status': 'error', 'message': 'search_type 必须为 image 或 video'}), 400

    libraries = list_retrieve_libraries(search_type)
    if library not in libraries:
        return jsonify({'status': 'error', 'message': f'无效检索库: {library}'}), 400

    session_key = 'image_retrieve_library' if search_type == 'image' else 'video_retrieve_library'
    session[session_key] = library
    session.modified = True
    return jsonify({'status': 'success', 'search_type': search_type, 'selected': library})


# ==============================================================================
# 检索 API
# ==============================================================================

@retrieve_blueprint.route('/retrieve/search', methods=['POST'])
def retrieve_search():
    """
    统一检索接口，检索结果保存到 retrieve_data 表
    """
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    if 'image' not in request.files or request.files['image'].filename == '':
        return jsonify({'status': 'error', 'message': '请上传文件'}), 400

    file = request.files['image']
    filename = file.filename
    search_type = request.form.get('search_type', 'auto')
    top_k = int(request.form.get('top_k', 10))

    file_type = get_file_type(filename)

    if search_type == 'auto':
        if file_type == 'unknown':
            return jsonify({
                'status': 'error',
                'message': f'不支持的文件类型: {filename}'
            }), 400
        search_type = file_type
    elif search_type == 'image' and file_type != 'image':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400
    elif search_type == 'video' and file_type != 'video':
        return jsonify({'status': 'error', 'message': '请上传视频文件'}), 400

    user_info = session['user_info']
    phone = user_info.get('phone', '')
    userid = user_info.get('Userid')
    openid = user_info.get('openid', '')

    # 保存上传文件
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    safe_name = f"{uuid.uuid4().hex[:12]}.{ext}"
    save_dir = os.path.join(current_dir, '..', 'static', 'uploads', phone, 'retrieve')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, safe_name)
    file.save(save_path)

    file_size = get_file_size_str(save_path)
    relative_path = os.path.join('uploads', phone, 'retrieve', safe_name).replace('\\', '/')
    query_file_url = f"/static/{relative_path}"

    session_key = 'image_retrieve_library' if search_type == 'image' else 'video_retrieve_library'
    requested_dataset = (request.form.get('dataset') or '').strip()
    if requested_dataset:
        libraries = list_retrieve_libraries(search_type)
        if requested_dataset not in libraries:
            return jsonify({'status': 'error', 'message': f'无效检索库: {requested_dataset}'}), 400
        session[session_key] = requested_dataset
        session.modified = True
        dataset = requested_dataset
    else:
        dataset = (session.get(session_key) or '').strip()
    try:
        if not dataset:
            return jsonify({'status': 'error', 'message': '未选择检索库'}), 400

        results = _build_local_retrieve_results(
            search_type=search_type,
            dataset=dataset,
            query_file_path=save_path,
            top_k=top_k,
        )
        results = _normalize_results_paths(results, search_type)
        results = _normalize_similarity_scores(results)
        if top_k > 0:
            results = results[:top_k]

        # 校验本地检索结果是否属于指定检索库
        if dataset and results:
            first_id = str(results[0].get('id', '')).strip()
            expected_prefix = f'{dataset}/'
            if first_id and not first_id.startswith(expected_prefix):
                return jsonify({
                    'status': 'error',
                    'message': (
                        f'本地检索结果未匹配到检索库 "{dataset}"。'
                        f'当前返回结果来自 "{first_id.split("/", 1)[0] if "/" in first_id else first_id}"。'
                        '请检查本机检索库目录结构和索引文件。'
                    )
                }), 502

        base_url = '/retrieve/library-file/image/' if search_type == 'image' else '/retrieve/library-file/video/'

        # ====================== 保存到 retrieve_data 表 ======================
        now = get_now_str()
        results_json = json.dumps(results, ensure_ascii=False)

        save_sql = """
            INSERT INTO retrieve_data
            (createtime, filename, search_type, result_count, top_k,
             openid, phone, file_size, results_json, Userid)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        excute_sql(save_sql, (
            now, safe_name, search_type, len(results), top_k,
            openid, phone, file_size, results_json, userid,
        ), fetch=False)

        return jsonify({
            'status': 'success',
            'message': f'{"以图搜图" if search_type == "image" else "以视频搜视频"}检索完成',
            'search_type': search_type,
            'dataset': dataset,
            'query_file_url': query_file_url,
            'base_url': base_url,
            'results': results,
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'检索过程中发生错误: {str(e)}'}), 500
