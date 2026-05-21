import os
import io
import logging
import requests
from PIL import Image, UnidentifiedImageError
import pdfplumber
from docx import Document
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ImageExtractor:
    """
    图片提取工具类，用于从PDF、Word文档和URL网页中提取图片
    每个提取方法返回 (图片数量, 图片文件名列表)，文件名统一规范
    新增：过滤分辨率——总面积≥128×128（16384像素）且长/宽均≤4096像素
    """

    def __init__(self, output_dir="extracted_images"):
        """初始化图片提取器"""
        self.output_dir = output_dir
        self._ensure_dir_exists(output_dir)
        # 分辨率过滤参数（核心修改：最小总面积改为128×128=16384像素）
        self.MIN_TOTAL_PIXELS = 128 * 128  # 最小总像素数（16384），支持64×256等非对称尺寸
        self.MAX_SINGLE_DIMENSION = 4096  # 单维度最大像素数（长/宽均≤4096）
        # 创建带重试机制的requests会话
        self.session = self._create_retry_session()

    def _create_retry_session(self, retries=3, backoff_factor=0.3):
        """创建带有重试机制的请求会话，提高网络图片下载稳定性"""
        session = requests.Session()
        retry_strategy = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _ensure_dir_exists(self, directory):
        """确保目录存在，如果不存在则创建"""
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"创建图片保存目录: {directory}")

    def _validate_image_data(self, image_data):
        """
        验证图片数据是否有效，并返回图片尺寸（宽×高）
        返回: (是否有效, 图片格式, 图片宽度, 图片高度)
        """
        try:
            # 验证图片完整性并获取尺寸
            with Image.open(io.BytesIO(image_data)) as img:
                img.verify()  # 检查图片是否损坏

                # 重新打开图片以获取格式和尺寸（verify后需要重新加载）
                img = Image.open(io.BytesIO(image_data))
                img_format = img.format.lower() if img.format else None
                width, height = img.size  # 获取图片分辨率（宽×高）
                return True, img_format, width, height
        except (UnidentifiedImageError, IOError, SyntaxError) as e:
            logger.warning(f"图片数据无效: {str(e)}")
            return False, None, 0, 0

    def _check_resolution_valid(self, width, height):
        """
        检查图片分辨率是否符合要求：
        1. 总面积 ≥ 128×128（16384像素）（支持64×256等非对称尺寸）
        2. 宽度 ≤ 4096 且 高度 ≤ 4096
        返回: 是否符合要求
        """
        total_pixels = width * height
        # 检查总面积和单维度限制（核心修改：日志提示更新为16384像素）
        if total_pixels < self.MIN_TOTAL_PIXELS:
            logger.warning(f"图片分辨率过小（{width}×{height}，总面积{total_pixels}），跳过（需≥16384像素）")
            return False
        if width > self.MAX_SINGLE_DIMENSION or height > self.MAX_SINGLE_DIMENSION:
            logger.warning(f"图片分辨率过大（{width}×{height}），跳过（单维度需≤4096像素）")
            return False
        return True

    def _save_image_safely(self, image_data, output_path):
        """
        安全保存图片，确保数据完整写入
        返回: 是否保存成功
        """
        try:
            with open(output_path, 'wb') as f:
                written_bytes = f.write(image_data)
                # 检查是否完整写入
                if written_bytes != len(image_data):
                    logger.error(f"图片写入不完整，预期{len(image_data)}字节，实际写入{written_bytes}字节")
                    if os.path.exists(output_path):
                        os.remove(output_path)  # 删除不完整文件
                    return False
            return True
        except IOError as e:
            logger.error(f"保存图片失败: {str(e)}")
            return False

    def extract_from_pdf(self, pdf_path, output_dir=None):
        """从PDF文件中提取图片，返回(图片数量, 图片文件名列表)"""
        file_names = []
        output_dir = output_dir or self.output_dir
        self._ensure_dir_exists(output_dir)

        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF文件不存在: {pdf_path}")

        try:
            count = 0  # 图片计数器，用于命名

            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    images = page.images
                    if not images:
                        continue

                    for img in images:
                        try:
                            # 获取图片数据流
                            img_data = img["stream"].get_data()
                            if not img_data:
                                logger.warning(f"PDF页面 {page_num} 存在空图片数据，跳过")
                                continue

                            # 1. 验证图片数据有效性 + 获取尺寸
                            is_valid, img_format, width, height = self._validate_image_data(img_data)
                            if not is_valid:
                                continue

                            # 2. 检查分辨率是否符合要求（总面积≥16384像素）
                            if not self._check_resolution_valid(width, height):
                                continue

                            # 3. 确定图片格式（优先使用验证得到的格式）
                            if not img_format:
                                if img_data.startswith(b'\xff\xd8'):
                                    img_format = 'jpg'
                                elif img_data.startswith(b'\x89PNG'):
                                    img_format = 'png'
                                else:
                                    img_format = 'png'  # 默认格式

                            # 4. 生成文件名并保存（统一命名规范：pdf_image+序号.格式）
                            count += 1
                            img_filename = f"pdf_image{count}.{img_format}"
                            img_path = os.path.join(output_dir, img_filename)

                            if self._save_image_safely(img_data, img_path):
                                file_names.append('/' + img_filename)
                                logger.info(f"成功提取PDF图片: {img_path}（分辨率：{width}×{height}）")
                            else:
                                count -= 1  # 保存失败回滚计数

                        except Exception as e:
                            logger.error(f"提取PDF页面 {page_num} 图片失败: {str(e)}")
                            continue

            logger.info(f"PDF图片提取完成，共提取 {count} 张有效图片（符合分辨率要求）")
            return count, file_names

        except Exception as e:
            logger.error(f"PDF提取过程出错: {str(e)}")
            raise

    def extract_from_word(self, word_path, output_dir=None):
        """从Word文件中提取图片，返回(图片数量, 图片文件名列表)"""
        file_names = []
        output_dir = output_dir or self.output_dir
        self._ensure_dir_exists(output_dir)

        if not os.path.exists(word_path):
            raise FileNotFoundError(f"Word文件不存在: {word_path}")

        if not word_path.endswith('.docx'):
            raise ValueError("只支持.docx格式的Word文件")

        try:
            doc = Document(word_path)
            count = 0  # 图片计数器，用于命名

            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    try:
                        image_part = rel.target_part
                        image_data = image_part._blob

                        # 1. 验证图片数据 + 获取尺寸
                        is_valid, img_format, width, height = self._validate_image_data(image_data)
                        if not is_valid:
                            continue

                        # 2. 检查分辨率是否符合要求（总面积≥16384像素）
                        if not self._check_resolution_valid(width, height):
                            continue

                        # 3. 确定图片格式
                        if not img_format:
                            content_type = image_part.content_type
                            img_format = content_type.split('/')[-1]
                            if img_format == 'jpeg':
                                img_format = 'jpg'

                        # 4. 生成文件名并保存（统一命名规范：word_image+序号.格式）
                        count += 1
                        img_filename = f"word_image{count}.{img_format}"
                        img_path = os.path.join(output_dir, img_filename)

                        if self._save_image_safely(image_data, img_path):
                            file_names.append('/' + img_filename)
                            logger.info(f"成功提取Word图片: {img_path}（分辨率：{width}×{height}）")
                        else:
                            count -= 1  # 保存失败回滚计数

                    except Exception as e:
                        logger.error(f"提取Word图片失败: {str(e)}")
                        continue

            logger.info(f"Word图片提取完成，共提取 {count} 张有效图片（符合分辨率要求）")
            return count, file_names

        except Exception as e:
            logger.error(f"Word提取过程出错: {str(e)}")
            raise

    def extract_from_url(self, url, output_dir=None, max_images=None, timeout=10):
        """从URL网页中提取图片，返回(图片数量, 图片文件名列表)"""
        file_names = []
        output_dir = output_dir or self.output_dir
        self._ensure_dir_exists(output_dir)

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
            }

            # 获取网页内容
            response = self.session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            img_tags = soup.find_all('img')
            count = 0  # 图片计数器，用于命名

            for img_tag in img_tags:
                if max_images is not None and count >= max_images:
                    break

                # 尝试从不同属性获取图片URL
                img_url = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-image')
                if not img_url:
                    continue

                # 处理相对路径
                full_img_url = urljoin(url, img_url)
                try:
                    # 下载图片（带重试机制）
                    img_response = self.session.get(
                        full_img_url,
                        headers=headers,
                        timeout=timeout,
                        stream=True
                    )
                    img_response.raise_for_status()

                    # 检查内容类型是否为图片
                    content_type = img_response.headers.get('content-type', '')
                    if not content_type.startswith('image/'):
                        logger.warning(f"跳过非图片资源: {full_img_url} (Content-Type: {content_type})")
                        continue

                    # 读取图片数据
                    image_data = img_response.content
                    if not image_data:
                        logger.warning(f"图片数据为空: {full_img_url}")
                        continue

                    # 1. 验证图片数据 + 获取尺寸
                    is_valid, img_format, width, height = self._validate_image_data(image_data)
                    if not is_valid:
                        continue

                    # 2. 检查分辨率是否符合要求（总面积≥16384像素）
                    if not self._check_resolution_valid(width, height):
                        continue

                    # 3. 确定图片格式
                    if not img_format:
                        img_format = content_type.split('/')[-1]
                        if img_format == 'jpeg':
                            img_format = 'jpg'

                    # 4. 生成文件名并保存（统一命名规范：url_image+序号.格式）
                    count += 1
                    img_filename = f"url_image{count}.{img_format}"
                    img_path = os.path.join(output_dir, img_filename)

                    if self._save_image_safely(image_data, img_path):
                        file_names.append('/' + img_filename)
                        logger.info(f"成功下载网页图片: {img_path}（分辨率：{width}×{height}）")
                    else:
                        count -= 1  # 保存失败回滚计数

                except Exception as e:
                    logger.error(f"处理图片 {full_img_url} 失败: {str(e)}")
                    continue

            logger.info(f"网页图片提取完成，共提取 {count} 张有效图片（符合分辨率要求）")
            return count, file_names

        except Exception as e:
            logger.error(f"网页图片提取过程出错: {str(e)}")
            raise


# 使用示例
if __name__ == "__main__":
    extractor = ImageExtractor(output_dir="optimized_extracted_images")

    try:
        # 示例1：从PDF提取图片（取消注释使用）
        # pdf_count, pdf_files = extractor.extract_from_pdf("example.pdf")
        # print(f"从PDF提取了 {pdf_count} 张符合要求的图片")
        # print("图片文件列表:", pdf_files)

        # 示例2：从Word提取图片（取消注释使用）
        # word_count, word_files = extractor.extract_from_word("example.docx")
        # print(f"从Word提取了 {word_count} 张符合要求的图片")
        # print("图片文件列表:", word_files)

        # 示例3：从URL提取图片（最多5张，符合分辨率要求）
        url_count, url_files = extractor.extract_from_url(
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            max_images=5
        )
        print(f"从URL提取了 {url_count} 张符合要求的图片")
        print("图片文件列表:", url_files)

    except Exception as e:
        logger.error(f"操作失败: {str(e)}")