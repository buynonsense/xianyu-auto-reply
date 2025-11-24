"""
AI回复引擎模块
集成XianyuAutoAgent的AI回复功能到现有项目中
"""

import os
import json
import time
import re
import requests
from typing import List, Dict, Optional, Any
from loguru import logger
from openai import OpenAI
from db_manager import db_manager


class AIReplyEngine:
    """AI回复引擎"""
    
    def __init__(self):
        self.clients = {}  # 存储不同账号的OpenAI客户端
        self.agents = {}   # 存储不同账号的Agent实例
        self.client_last_used = {}  # 记录客户端最后使用时间 {cookie_id: timestamp}
        self._init_default_prompts()
    
    def _init_default_prompts(self):
        """初始化默认提示词"""
        self.default_prompts = {
            'classify': '''你是一个意图分类专家，需要判断用户消息的意图类型。
请根据用户消息内容，返回以下意图之一：
- price: 价格相关（议价、优惠、降价等）
- tech: 技术相关（产品参数、使用方法、故障等）
- default: 其他一般咨询

只返回意图类型，不要其他内容。''',
            
            'price': '''你是一位经验丰富的销售专家，擅长议价。
语言要求：简短直接，每句≤10字，总字数≤40字。
议价策略：
1. 根据议价次数递减优惠：第1次小幅优惠，第2次中等优惠，第3次最大优惠
2. 接近最大议价轮数时要坚持底线，强调商品价值
3. 优惠不能超过设定的最大百分比和金额
4. 语气要友好但坚定，突出商品优势
注意：结合商品信息、对话历史和议价设置，给出合适的回复。''',
            
            'tech': '''你是一位技术专家，专业解答产品相关问题。
语言要求：简短专业，每句≤10字，总字数≤40字。
回答重点：产品功能、使用方法、注意事项。
注意：基于商品信息回答，避免过度承诺。''',
            
            'default': '''你是一位资深电商卖家，提供优质客服。
语言要求：简短友好，每句≤10字，总字数≤40字。
回答重点：商品介绍、物流、售后等常见问题。
注意：结合商品信息，给出实用建议。'''
        }
    
    def get_client(self, cookie_id: str) -> Optional[OpenAI]:
        """获取指定账号的OpenAI客户端"""
        if cookie_id not in self.clients:
            settings = db_manager.get_ai_reply_settings(cookie_id)
            if not settings['ai_enabled'] or not settings['api_key']:
                return None
            
            try:
                logger.info(f"创建OpenAI客户端 {cookie_id}: base_url={settings['base_url']}, api_key={'***' + settings['api_key'][-4:] if settings['api_key'] else 'None'}")
                self.clients[cookie_id] = OpenAI(
                    api_key=settings['api_key'],
                    base_url=settings['base_url']
                )
                logger.info(f"为账号 {cookie_id} 创建OpenAI客户端成功，实际base_url: {self.clients[cookie_id].base_url}")
            except Exception as e:
                logger.error(f"创建OpenAI客户端失败 {cookie_id}: {e}")
                return None
        
        # 记录使用时间
        self.client_last_used[cookie_id] = time.time()
        return self.clients[cookie_id]

    def _is_dashscope_api(self, settings: dict) -> bool:
        """判断是否为DashScope API - 只有选择自定义模型时才使用"""
        model_name = settings.get('model_name', '')
        base_url = settings.get('base_url', '')

        # 只有当模型名称为"custom"或"自定义"时，才使用DashScope API格式
        # 其他情况都使用OpenAI兼容格式
        is_custom_model = model_name.lower() in ['custom', '自定义', 'dashscope', 'qwen-custom']
        is_dashscope_url = 'dashscope.aliyuncs.com' in base_url

        logger.info(f"API类型判断: model_name={model_name}, is_custom_model={is_custom_model}, is_dashscope_url={is_dashscope_url}")

        return is_custom_model and is_dashscope_url

    def _call_dashscope_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用DashScope API"""
        # 提取app_id从base_url
        base_url = settings['base_url']
        if '/apps/' in base_url:
            app_id = base_url.split('/apps/')[-1].split('/')[0]
        else:
            raise ValueError("DashScope API URL中未找到app_id")

        # 构建请求URL
        url = f"https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"

        # 构建提示词（将messages合并为单个prompt）
        system_content = ""
        user_content = ""

        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            elif msg['role'] == 'user':
                user_content = msg['content']

        # 构建更清晰的prompt格式
        if system_content and user_content:
            prompt = f"{system_content}\n\n用户问题：{user_content}\n\n请直接回答用户的问题："
        elif user_content:
            prompt = user_content
        else:
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        # 构建请求数据
        data = {
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "max_tokens": max_tokens,
                "temperature": temperature
            },
            "debug": {}
        }

        # 【新增】针对 GLM-4.5 系列尝试关闭思维链
        model_name = settings.get('model_name', '').lower()
        if 'glm-4.5' in model_name:
            logger.info(f"检测到 GLM-4.5 模型 ({model_name})，尝试在 DashScope 请求中关闭思维链")
            # DashScope 的参数结构可能略有不同，尝试放在 parameters 中
            data["parameters"]["thinking"] = {"type": "disabled"}

        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }

        logger.info(f"DashScope API请求: {url}")
        logger.info(f"发送的prompt: {prompt}")
        logger.debug(f"请求数据: {json.dumps(data, ensure_ascii=False)}")

        response = requests.post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"DashScope API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"DashScope API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        logger.debug(f"DashScope API响应: {json.dumps(result, ensure_ascii=False)}")

        # 提取回复内容
        if 'output' in result and 'text' in result['output']:
            return result['output']['text'].strip()
        else:
            raise Exception(f"DashScope API响应格式错误: {result}")

    def _clean_thinking_content(self, text: str) -> str:
        """清理模型输出中的 <think>...</think> 标签内容"""
        if not text:
            return ""
        # 使用正则移除 <think> 标签及其内容 (DOTALL模式让.匹配换行符)
        cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return cleaned_text.strip()

    def _extract_response_content(self, response: Any) -> str:
        """
        智能提取响应内容，兼容普通模型和思考模型(Reasoning Models)
        """
        try:
            message = response.choices[0].message
            
            # 1. 获取 content 字段 (标准字段)
            content = getattr(message, 'content', '')
            
            # 2. 获取 reasoning_content 字段 (DeepSeek/OneAPI 等思考模型专用)
            reasoning = getattr(message, 'reasoning_content', '')
            
            logger.info(f"🔍 [提取前状态] content长度={len(str(content))}, reasoning长度={len(str(reasoning))}")

            final_reply = ""

            if content:
                # 如果 content 中包含 <think> 标签，说明是混合输出，需要清洗
                if '<think>' in content:
                    logger.info("检测到 <think> 标签，正在清洗思考过程...")
                    final_reply = self._clean_thinking_content(content)
                else:
                    final_reply = content.strip()
            
            if not final_reply:
                # 如果 content 为空，但有 reasoning
                if reasoning:
                    logger.warning("⚠️ 模型仅返回了思考过程(reasoning_content)，未返回最终回复(content)。这通常是因为Token不足导致生成被截断。")
                    return ""
                else:
                    logger.warning("❌ 模型返回的 content 和 reasoning_content 均为空！可能被过滤或模型出错。")
                    return ""

            return final_reply

        except Exception as e:
            logger.error(f"解析响应内容失败: {e}")
            logger.debug(f"原始响应对象: {response}")
            return ""

    def _call_openai_api(self, client: OpenAI, settings: dict, messages: list,
                     max_tokens: int = 100, temperature: float = 0.7) -> str:
        """
        统一使用 chat.completions.create 调用 OpenAI 兼容接口
        """
        model = settings['model_name']
        
        try:
            # 【新增】针对 GLM-4.5 系列尝试关闭思维链
            extra_body = {}
            if 'glm-4.5' in model.lower():
                logger.info(f"检测到 GLM-4.5 模型 ({model})，尝试通过 extra_body 关闭思维链")
                extra_body["thinking"] = {"type": "disabled"}

            logger.info(f"🚀 [发起请求] 模型: {model}, MaxTokens: {max_tokens}, Temp: {temperature}")
            
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra_body if extra_body else None  # 注入额外参数
            )
            
            # ================== 暴力调试 ==================
            try:
                if hasattr(resp, 'model_dump_json'):
                    raw_json = resp.model_dump_json(indent=2)
                    logger.info(f"🔍 [原始AI响应 JSON]:\n{raw_json}")
                else:
                    logger.info(f"🔍 [原始AI响应 Raw]: {resp}")
            except Exception as dump_err:
                logger.error(f"无法序列化响应对象: {dump_err}")
            # ============================================
            
            # 使用增强的提取逻辑
            reply = self._extract_response_content(resp)
            
            if not reply:
                logger.warning(f"⚠️ 模型 {model} 返回了空内容，请检查上方的 [原始AI响应]")
            
            return reply

        except Exception as e:
            logger.error(f"OpenAI API调用失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    def is_ai_enabled(self, cookie_id: str) -> bool:
        """检查指定账号是否启用AI回复"""
        settings = db_manager.get_ai_reply_settings(cookie_id)
        return settings['ai_enabled']
    
    def detect_intent(self, message: str, cookie_id: str) -> str:
        """检测用户消息意图"""
        try:
            settings = db_manager.get_ai_reply_settings(cookie_id)
            if not settings['ai_enabled'] or not settings['api_key']:
                return 'default'

            custom_prompts = json.loads(settings['custom_prompts']) if settings['custom_prompts'] else {}
            classify_prompt = custom_prompts.get('classify', self.default_prompts['classify'])

            # 打印调试信息
            logger.info(f"AI设置调试 {cookie_id}: base_url={settings['base_url']}, model={settings['model_name']}")

            messages = [
                {"role": "system", "content": classify_prompt},
                {"role": "user", "content": message}
            ]

            # 根据API类型选择调用方式
            if self._is_dashscope_api(settings):
                logger.info(f"使用DashScope API进行意图检测")
                # DashScope 暂时保持较小 Token，如果有问题也需要调大
                response_text = self._call_dashscope_api(settings, messages, max_tokens=100, temperature=0.1)
            else:
                logger.info(f"使用OpenAI兼容API进行意图检测")
                client = self.get_client(cookie_id)
                if not client:
                    return 'default'
                
                # 仍然保持较大的 token 以防参数注入失败，确保稳定性
                response_text = self._call_openai_api(client, settings, messages, max_tokens=1000, temperature=0.1)

            intent = response_text.lower()
            # 简单清洗可能存在的标点
            intent = intent.replace('.', '').replace('。', '').strip()
            
            if 'price' in intent or '价格' in intent:
                return 'price'
            elif 'tech' in intent or '技术' in intent:
                return 'tech'
            else:
                # 兜底逻辑：如果AI废话太多没返回标准关键词，但包含了相关字眼
                if 'default' in intent or '其他' in intent:
                    return 'default'
                return 'default'

        except Exception as e:
            logger.error(f"意图检测失败 {cookie_id}: {e}")
            return 'default'
    
    def generate_reply(self, message: str, item_info: dict, chat_id: str,
                      cookie_id: str, user_id: str, item_id: str) -> Optional[str]:
        """生成AI回复"""
        if not self.is_ai_enabled(cookie_id):
            return None
        
        try:
            # 1. 获取AI回复设置
            settings = db_manager.get_ai_reply_settings(cookie_id)

            # 2. 检测意图
            intent = self.detect_intent(message, cookie_id)
            logger.info(f"检测到意图: {intent} (账号: {cookie_id})")

            # 3. 获取对话历史
            context = self.get_conversation_context(chat_id, cookie_id)

            # 4. 获取议价次数
            bargain_count = self.get_bargain_count(chat_id, cookie_id)

            # 5. 检查议价轮数限制
            if intent == "price":
                max_bargain_rounds = settings.get('max_bargain_rounds', 3)
                if bargain_count >= max_bargain_rounds:
                    logger.info(f"议价次数已达上限 ({bargain_count}/{max_bargain_rounds})，拒绝继续议价")
                    # 返回拒绝议价的回复
                    refuse_reply = f"抱歉，这个价格已经是最优惠的了，不能再便宜了哦！"
                    # 保存对话记录
                    self.save_conversation(chat_id, cookie_id, user_id, item_id, "user", message, intent)
                    self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", refuse_reply, intent)
                    return refuse_reply

            # 6. 构建提示词
            custom_prompts = json.loads(settings['custom_prompts']) if settings['custom_prompts'] else {}
            system_prompt = custom_prompts.get(intent, self.default_prompts[intent])

            # 7. 构建商品信息
            item_desc = f"商品标题: {item_info.get('title', '未知')}\n"
            item_desc += f"商品价格: {item_info.get('price', '未知')}元\n"
            item_desc += f"商品描述: {item_info.get('desc', '无')}"

            # 8. 构建对话历史
            context_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context[-10:]])  # 最近10条

            # 9. 构建用户消息
            max_bargain_rounds = settings.get('max_bargain_rounds', 3)
            max_discount_percent = settings.get('max_discount_percent', 10)
            max_discount_amount = settings.get('max_discount_amount', 100)

            user_prompt = f"""商品信息：
{item_desc}

对话历史：
{context_str}

议价设置：
- 当前议价次数：{bargain_count}
- 最大议价轮数：{max_bargain_rounds}
- 最大优惠百分比：{max_discount_percent}%
- 最大优惠金额：{max_discount_amount}元

用户消息：{message}

请根据以上信息生成回复："""

            # 10. 调用AI生成回复
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 根据API类型选择调用方式
            reply = ""
            if self._is_dashscope_api(settings):
                logger.info(f"使用DashScope API生成回复")
                reply = self._call_dashscope_api(settings, messages, max_tokens=2000, temperature=0.7)
            else:
                logger.info(f"使用OpenAI兼容API生成回复")
                client = self.get_client(cookie_id)
                if not client:
                    return None
                # 【重要修复】生成回复时 Token 也要给足，改为 2000
                reply = self._call_openai_api(client, settings, messages, max_tokens=2000, temperature=0.7)

            # 检查回复是否为空
            if not reply:
                logger.warning(f"AI回复生成为空 (账号: {cookie_id})")
                return None

            # 11. 保存对话记录
            self.save_conversation(chat_id, cookie_id, user_id, item_id, "user", message, intent)
            self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", reply, intent)

            # 12. 更新议价次数
            if intent == "price":
                self.increment_bargain_count(chat_id, cookie_id)
            
            logger.info(f"AI回复生成成功 (账号: {cookie_id}): {reply}")
            return reply
            
        except Exception as e:
            logger.error(f"AI回复生成失败 {cookie_id}: {e}")
            return None
    
    def get_conversation_context(self, chat_id: str, cookie_id: str, limit: int = 20) -> List[Dict]:
        """获取对话上下文"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT role, content FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? 
                ORDER BY created_at DESC LIMIT ?
                ''', (chat_id, cookie_id, limit))
                
                results = cursor.fetchall()
                # 反转顺序，使其按时间正序
                context = [{"role": row[0], "content": row[1]} for row in reversed(results)]
                return context
        except Exception as e:
            logger.error(f"获取对话上下文失败: {e}")
            return []
    
    def save_conversation(self, chat_id: str, cookie_id: str, user_id: str, 
                         item_id: str, role: str, content: str, intent: str = None):
        """保存对话记录"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                INSERT INTO ai_conversations 
                (cookie_id, chat_id, user_id, item_id, role, content, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (cookie_id, chat_id, user_id, item_id, role, content, intent))
                db_manager.conn.commit()
        except Exception as e:
            logger.error(f"保存对话记录失败: {e}")
    
    def get_bargain_count(self, chat_id: str, cookie_id: str) -> int:
        """获取议价次数"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT COUNT(*) FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? AND intent = 'price' AND role = 'user'
                ''', (chat_id, cookie_id))
                
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"获取议价次数失败: {e}")
            return 0
    
    def increment_bargain_count(self, chat_id: str, cookie_id: str):
        """增加议价次数（通过保存记录自动增加）"""
        # 议价次数通过查询price意图的用户消息数量来计算，无需单独操作
        pass
    
    def clear_client_cache(self, cookie_id: str = None):
        """清理客户端缓存"""
        if cookie_id:
            self.clients.pop(cookie_id, None)
            self.client_last_used.pop(cookie_id, None)
            logger.info(f"清理账号 {cookie_id} 的客户端缓存")
        else:
            self.clients.clear()
            self.client_last_used.clear()
            logger.info("清理所有客户端缓存")
    
    def cleanup_unused_clients(self, max_idle_hours: int = 24):
        """清理长时间未使用的客户端（防止内存泄漏）
        
        Args:
            max_idle_hours: 最大空闲时间（小时），默认24小时
        """
        try:
            current_time = time.time()
            max_idle_seconds = max_idle_hours * 3600
            
            # 找出超过最大空闲时间的客户端
            expired_clients = [
                cookie_id for cookie_id, last_used in self.client_last_used.items()
                if current_time - last_used > max_idle_seconds
            ]
            
            # 清理过期客户端
            for cookie_id in expired_clients:
                self.clients.pop(cookie_id, None)
                self.client_last_used.pop(cookie_id, None)
                self.agents.pop(cookie_id, None)
            
            if expired_clients:
                logger.info(f"AI回复引擎：清理了 {len(expired_clients)} 个长时间未使用的客户端")
                logger.debug(f"清理的账号: {expired_clients}")
                logger.debug(f"当前活跃客户端数量: {len(self.clients)}")
            
        except Exception as e:
            logger.error(f"AI回复引擎：清理未使用客户端时出错: {e}")


# 全局AI回复引擎实例
ai_reply_engine = AIReplyEngine()