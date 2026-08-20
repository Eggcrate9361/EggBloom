#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ComfyUI API 客户端 - 用于远程文生图
适配 Anima 正式版工作流
"""

import json
import time
import urllib.request
import urllib.error
import uuid
import os
import ssl

class ComfyUIClient:
    """ComfyUI HTTP API 客户端"""
    
    def __init__(self, host, port=8188, timeout=120):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
    
    def _request(self, path, method="GET", data=None):
        """发送 HTTP 请求"""
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        
        if data is not None:
            data = json.dumps(data).encode("utf-8")
        
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise ConnectionError(f"ComfyUI {path} HTTP {e.code}: {body or e.reason}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"ComfyUI 连接失败: {e}")
    
    def is_connected(self):
        """检查 ComfyUI 是否在线"""
        try:
            self._request("/system_stats")
            return True
        except:
            return False
    
    def queue_prompt(self, workflow, client_id=None):
        """提交工作流到队列，返回 prompt_id"""
        if client_id is None:
            client_id = str(uuid.uuid4())
        
        data = {
            "prompt": workflow,
            "client_id": client_id
        }
        
        result = self._request("/prompt", method="POST", data=data)
        prompt_id = result.get("prompt_id")
        
        if not prompt_id:
            raise RuntimeError(f"提交工作流失败: {result}")
        
        return prompt_id

    def upload_image(self, image_path, subfolder="", image_type="input"):
        """上传图片到 ComfyUI input 目录，返回文件名"""
        import mimetypes
        filename = os.path.basename(image_path)
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        
        boundary = "----EggBloomUpload"
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + image_data + f"\r\n--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="subfolder"\r\n\r\n{subfolder}\r\n'.encode("utf-8")
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\n{image_type}\r\n--{boundary}--\r\n".encode("utf-8")
        
        url = f"{self.base_url}/upload/image"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        with urllib.request.urlopen(req, timeout=30, context=self.ctx) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        
        uploaded_name = result.get("name", filename)
        print(f"[ComfyUI] 图片上传成功: {uploaded_name}")
        return uploaded_name
    
    def get_history(self, prompt_id):
        """获取指定任务的历史记录"""
        return self._request(f"/history/{prompt_id}")
    
    def get_image(self, filename, subfolder="", folder_type="output"):
        """下载生成的图片"""
        params = f"filename={filename}&subfolder={subfolder}&type={folder_type}"
        url = f"{self.base_url}/view?{params}"
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30, context=self.ctx) as resp:
            return resp.read()
    
    def wait_and_get_images(self, prompt_id, timeout=None):
        """轮询等待任务完成，返回生成的图片路径列表"""
        if timeout is None:
            timeout = self.timeout
        
        start_time = time.time()
        poll_interval = 0.5
        
        while time.time() - start_time < timeout:
            history = self.get_history(prompt_id)
            
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                image_paths = []
                
                for node_id, node_output in outputs.items():
                    images = node_output.get("images", [])
                    for img in images:
                        filename = img.get("filename")
                        subfolder = img.get("subfolder", "")
                        
                        if filename:
                            img_data = self.get_image(filename, subfolder)
                            # 保存到 EggBloom temp 目录
                            egg_temp = os.path.join(
                                os.path.dirname(os.path.abspath(__file__)),
                                "temp"
                            )
                            os.makedirs(egg_temp, exist_ok=True)
                            local_path = os.path.join(egg_temp, filename)

                            with open(local_path, "wb") as f:
                                f.write(img_data)

                            image_paths.append(local_path)
                
                return image_paths
            
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, 2.0)
        
        raise TimeoutError(f"任务 {prompt_id} 超时（{timeout}秒）")


def frontend_to_api_workflow(frontend_wf):
    """将 ComfyUI 前端格式转换为 API 格式"""
    api_wf = {}

    nodes = frontend_wf.get("nodes", [])
    links = frontend_wf.get("links", [])

    # 建立 link_id -> (source_node_id, source_slot) 映射（必须先于节点过滤）
    link_map = {}
    for link in links:
        if len(link) >= 6:
            link_id, from_node, from_slot, to_node, to_slot, link_type = link
            link_map[link_id] = (from_node, from_slot)

    # 找出所有 bypassed 节点
    bypassed_nodes = {node["id"] for node in nodes if node.get("mode") == 4}

    # 建立 bypassed 节点的输入到输出的映射
    bypass_map = {}
    for node in nodes:
        if node["id"] in bypassed_nodes:
            node_inputs = {}
            for inp in node.get("inputs", []):
                link_id = inp.get("link")
                if link_id and link_id in link_map:
                    source_node, source_slot = link_map[link_id]
                    node_inputs[inp.get("name", "")] = (source_node, source_slot)
            bypass_map[node["id"]] = node_inputs

    # 不需要生成 API 节点的类型，但其输出链接仍保留在 link_map 中供下游解析
    SKIP_TYPES = {
        "Fast Groups Bypasser (rgthree)",
        "ShowText|pysssss",
    }

    for node in nodes:
        node_id = str(node.get("id"))
        node_type = node.get("type")
        widgets_values = node.get("widgets_values", [])
        inputs = node.get("inputs", [])

        # 跳过被禁用的节点 (mode=4 表示 bypass) 或不需要的节点
        if node.get("mode") == 4 or node_type in SKIP_TYPES:
            continue
        
        api_node = {
            "class_type": node_type,
            "inputs": {}
        }
        
        # 映射 widgets_values 到 inputs
        # CR Prompt Text 节点特殊处理
        if node_type == "CR Prompt Text":
            if widgets_values:
                api_node["inputs"]["prompt"] = widgets_values[0] if widgets_values[0] else ""
        # CLIPTextEncode 节点（负向提示词）
        elif node_type == "CLIPTextEncode":
            if widgets_values:
                api_node["inputs"]["text"] = widgets_values[0] if widgets_values[0] else ""
        # EmptyLatentImage 节点
        elif node_type == "EmptyLatentImage":
            if len(widgets_values) >= 3:
                api_node["inputs"]["width"] = widgets_values[0]
                api_node["inputs"]["height"] = widgets_values[1]
                api_node["inputs"]["batch_size"] = widgets_values[2]
        # KSampler 节点
        elif node_type == "KSampler":
            if len(widgets_values) >= 7:
                api_node["inputs"]["seed"] = widgets_values[0]
                api_node["inputs"]["steps"] = widgets_values[2]
                api_node["inputs"]["cfg"] = widgets_values[3]
                api_node["inputs"]["sampler_name"] = widgets_values[4]
                api_node["inputs"]["scheduler"] = widgets_values[5]
                api_node["inputs"]["denoise"] = widgets_values[6]
        # CLIPLoader
        elif node_type == "CLIPLoader":
            if widgets_values:
                api_node["inputs"]["clip_name"] = widgets_values[0]
                if len(widgets_values) > 1:
                    api_node["inputs"]["type"] = widgets_values[1]
        # UNETLoader
        elif node_type == "UNETLoader":
            if widgets_values:
                api_node["inputs"]["unet_name"] = widgets_values[0]
                if len(widgets_values) > 1:
                    api_node["inputs"]["weight_dtype"] = widgets_values[1]
        # VAELoader
        elif node_type == "VAELoader":
            if widgets_values:
                api_node["inputs"]["vae_name"] = widgets_values[0]
        # LoraLoaderModelOnly
        elif node_type == "LoraLoaderModelOnly":
            if widgets_values:
                lora_name = widgets_values[0]
                # 去除中文前缀（如"（美学品质修正）"）
                import re
                lora_name = re.sub(r'^[（(][^)）]*[)）]', '', lora_name).strip()
                api_node["inputs"]["lora_name"] = lora_name
                if len(widgets_values) > 1:
                    api_node["inputs"]["strength_model"] = widgets_values[1]
        # ModelSamplingAuraFlow
        elif node_type == "ModelSamplingAuraFlow":
            if widgets_values:
                api_node["inputs"]["shift"] = widgets_values[0]
        # JoinStringMulti - widgets_values 顺序实际是 [inputcount, delimiter, return_list]
        # 旧代码映射为 [delimiter, return_list, inputcount] 导致 delimiter=3, inputcount 缺失
        elif node_type == "JoinStringMulti":
            if widgets_values and len(widgets_values) >= 3:
                api_node["inputs"]["inputcount"] = int(widgets_values[0]) if widgets_values[0] is not None else 3
                api_node["inputs"]["delimiter"] = str(widgets_values[1]) if widgets_values[1] is not None else " "
                api_node["inputs"]["return_list"] = bool(widgets_values[2]) if widgets_values[2] is not None else False
        # UpscaleModelLoader
        elif node_type == "UpscaleModelLoader":
            if widgets_values:
                api_node["inputs"]["model_name"] = widgets_values[0]
        # UltimateSDUpscale
        elif node_type == "UltimateSDUpscale":
            if widgets_values:
                api_node["inputs"]["upscale_by"] = widgets_values[6]
                api_node["inputs"]["seed"] = widgets_values[7]
                api_node["inputs"]["steps"] = widgets_values[8]
                api_node["inputs"]["cfg"] = widgets_values[9]
                api_node["inputs"]["sampler_name"] = widgets_values[10]
                api_node["inputs"]["scheduler"] = widgets_values[11]
                api_node["inputs"]["denoise"] = widgets_values[12]
                api_node["inputs"]["mode_type"] = widgets_values[13]
                api_node["inputs"]["tile_width"] = widgets_values[14]
                api_node["inputs"]["tile_height"] = widgets_values[15]
                api_node["inputs"]["mask_blur"] = widgets_values[16]
                api_node["inputs"]["tile_padding"] = widgets_values[17]
                api_node["inputs"]["seam_fix_mode"] = widgets_values[18]
                api_node["inputs"]["seam_fix_denoise"] = widgets_values[19]
                api_node["inputs"]["seam_fix_width"] = widgets_values[20]
                api_node["inputs"]["seam_fix_mask_blur"] = widgets_values[21]
                api_node["inputs"]["seam_fix_padding"] = widgets_values[22]
                api_node["inputs"]["force_uniform_tiles"] = widgets_values[23]
                api_node["inputs"]["tiled_decode"] = widgets_values[24]
        # SaveImage
        elif node_type == "SaveImage":
            if widgets_values:
                api_node["inputs"]["filename_prefix"] = widgets_values[0]

        # 处理连接输入
        for inp in inputs:
            link_id = inp.get("link")
            if link_id and link_id in link_map:
                source_node, source_slot = link_map[link_id]
                # 如果源节点是 bypassed，追踪到真正的源
                if source_node in bypassed_nodes and source_node in bypass_map:
                    # 获取 bypassed 节点的第一个输入作为源
                    bypass_inputs = bypass_map[source_node]
                    if bypass_inputs:
                        real_source = list(bypass_inputs.values())[0]
                        source_node, source_slot = real_source
                api_node["inputs"][inp["name"]] = [str(source_node), source_slot]
        
        api_wf[node_id] = api_node
    
    return api_wf


def load_workflow(workflow_path):
    """加载工作流文件"""
    with open(workflow_path, "r", encoding="utf-8") as f:
        frontend_wf = json.load(f)
    return frontend_to_api_workflow(frontend_wf)


def set_prompt_in_workflow(api_wf, prompt_text, prompt_node_id="353"):
    """在指定节点设置 prompt 文本"""
    wf = json.loads(json.dumps(api_wf))
    
    if prompt_node_id in wf:
        wf[prompt_node_id]["inputs"]["prompt"] = prompt_text
    else:
        raise ValueError(f"节点 {prompt_node_id} 不存在于工作流中")
    
    return wf


def get_comfyui_client(config):
    """从配置创建 ComfyUI 客户端"""
    return ComfyUIClient(
        host=config.get("comfyui_host", "127.0.0.1"),
        port=config.get("comfyui_port", 8188),
        timeout=config.get("comfyui_timeout", 120)
    )
