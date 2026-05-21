import os
from flask import Blueprint, render_template, request, session, url_for, Response, stream_with_context
from imagedetection.NPRDeepfakeDetection.qwen_vl import qwen_vlAIGC
from imagedetection.NPRDeepfakeDetection.test import run_result
from imagedetection.views.image_utils import ImageExtractor
from imagedetection.views.utils import merge_images_corner
from imagedetection.realguard_competition.infer_api import build_infer_model, aigc_infer, generate_heatmap_for_image

batch_blueprint = Blueprint('batch_blueprint', __name__)
current_dir = os.path.dirname(os.path.abspath(__file__))


@batch_blueprint.route('/file')
def file():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('batch.html')


@batch_blueprint.route('/batch', methods=['POST'])
def batch():
    """ 上传文件后，跳转到流式展示页面 """
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')

    # 保存文件，传给 batch_stream 页面去处理
    if 'document_file' in request.files and request.files['document_file'].filename != '':
        document_file = request.files['document_file']
        filename = document_file.filename
        save_dir = os.path.join(current_dir, '..', 'static', 'uploads', 'file')
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        document_file.save(save_path)
        # 记录文件路径到 session，交给 batch_stream 使用
        session['upload_file_path'] = save_path
        return render_template("batch_stream.html")
    elif 'document_url' in request.form and request.form['document_url'].strip() != '':
        file_url = request.form.get('document_url')
        session['upload_file_url'] = file_url
        return render_template("batch_stream.html")

    return render_template('batch.html')


@batch_blueprint.route('/batch_stream')
def batch_stream():
    if 'upload_file_path' not in session and  'upload_file_url' not in session:
        return render_template("batch.html")
    image_save_dir = os.path.join(current_dir, '..', 'static', 'images')
    extractor = ImageExtractor(output_dir=image_save_dir)
    fake_path = os.path.join(current_dir, '..', 'static', 'system', 'fake.png')
    if 'upload_file_path' in session:
        file_path = session['upload_file_path']
        filename = os.path.basename(file_path)
        if filename.endswith('.pdf'):
            count, file_name = extractor.extract_from_pdf(file_path)
        elif filename.endswith('.docx'):
            count, file_name = extractor.extract_from_word(file_path)
        else:
            count, file_name = extractor.extract_from_doc(file_path)
        session.pop('upload_file_path', None)
    elif 'upload_file_url' in session:
        file_path = session['upload_file_url']
        count, file_name = extractor.extract_from_url(file_path)
        session.pop('upload_file_url', None)
    AIGC_MODEL, AIGC_TRANSFORM, AIGC_DEVICE = build_infer_model()


    def generate():
        for i in range(1, count + 1):
            img_path = image_save_dir + file_name[i - 1]
            prob_fake, pred_label = aigc_infer(img_path, AIGC_MODEL, AIGC_TRANSFORM, AIGC_DEVICE, threshold=0.5)
            model_result = round(prob_fake, 2)*100
            real_score = round(100 - model_result, 2)
            explanation = qwen_vlAIGC(img_path, model_result)

            if model_result >= 50:
                merge_images_corner(img_path, fake_path, img_path)

            # ⚡ SSE 数据推送（必须以 `data:` 开头，以 `\n\n` 结束）
            yield f"data: { {'index': i, 'fake': model_result, 'real': real_score, 'total': count, 'explanation': explanation, 'image': file_name[i-1]} }\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
