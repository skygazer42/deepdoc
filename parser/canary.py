# -*- coding: utf-8 -*-
"""
Canary 部署配置（升级计划 Step 7）

支持按百分比分流 v1/v2 API，用于灰度发布。
"""
import os
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Canary 配置
# CANARY_PERCENT: 0-100，v2 API 的流量百分比
# 0 = 全部走 v1，100 = 全部走 v2
CANARY_PERCENT = int(os.getenv("CANARY_PERCENT", "0"))

# CANARY_FORCE_V2: 强制所有请求走 v2（用于测试）
CANARY_FORCE_V2 = os.getenv("CANARY_FORCE_V2", "").lower() in ("1", "true", "yes")

# CANARY_FORCE_V1: 强制所有请求走 v1（用于回滚）
CANARY_FORCE_V1 = os.getenv("CANARY_FORCE_V1", "").lower() in ("1", "true", "yes")


def should_use_v2(request_id: Optional[str] = None) -> bool:
    """判断当前请求是否应该使用 v2 API

    Args:
        request_id: 请求 ID（用于确定性分流，可选）

    Returns:
        True 表示使用 v2，False 表示使用 v1
    """
    # 强制模式优先
    if CANARY_FORCE_V2:
        logger.debug("Canary: 强制 v2 (CANARY_FORCE_V2=1)")
        return True
    if CANARY_FORCE_V1:
        logger.debug("Canary: 强制 v1 (CANARY_FORCE_V1=1)")
        return False

    # 百分比分流
    if CANARY_PERCENT <= 0:
        return False
    if CANARY_PERCENT >= 100:
        return True

    # 如果有 request_id，使用确定性分流（同一请求总是走同一版本）
    if request_id:
        import hashlib
        hash_val = int(hashlib.md5(request_id.encode()).hexdigest(), 16) % 100
        use_v2 = hash_val < CANARY_PERCENT
        logger.debug("Canary: request_id=%s hash=%d%% < %d%% -> v2=%s",
                     request_id, hash_val, CANARY_PERCENT, use_v2)
        return use_v2

    # 随机分流
    use_v2 = random.randint(0, 99) < CANARY_PERCENT
    logger.debug("Canary: random=%d%% < %d%% -> v2=%s",
                 random.randint(0, 99), CANARY_PERCENT, use_v2)
    return use_v2


def get_canary_config() -> dict:
    """获取当前 canary 配置（用于日志/监控）"""
    return {
        "canary_percent": CANARY_PERCENT,
        "force_v2": CANARY_FORCE_V2,
        "force_v1": CANARY_FORCE_V1,
    }
