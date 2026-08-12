# -*- coding: utf-8 -*-
"""
任务队列管理（升级计划 Step 4）

支持两种后端：
1. InMemoryQueue: 开发/测试环境，无需 Redis
2. RedisQueue: 生产环境，需要 Redis 服务

用法：
    from parser.task_queue import get_queue
    queue = get_queue()
    task_id = queue.enqueue(parse_task, args=(...))
    status = queue.get_status(task_id)
"""
import json
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo:
    """任务信息"""
    def __init__(self, task_id: str, func_name: str, args: tuple = (), kwargs: dict = None):
        self.task_id = task_id
        self.func_name = func_name
        self.args = args
        self.kwargs = kwargs or {}
        self.status = TaskStatus.PENDING
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.started_at = None
        self.completed_at = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "func_name": self.func_name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class InMemoryQueue:
    """内存队列（开发/测试用）"""

    def __init__(self):
        self._tasks: Dict[str, TaskInfo] = {}
        self._queue: list = []
        logger.info("初始化内存任务队列")

    def enqueue(self, func: Callable, args: tuple = (), kwargs: dict = None,
                task_id: str = None) -> str:
        """入队任务"""
        task_id = task_id or str(uuid.uuid4())
        task = TaskInfo(task_id, func.__name__, args, kwargs)
        self._tasks[task_id] = task
        self._queue.append(task_id)
        logger.debug("任务入队: %s (%s)", task_id, func.__name__)
        return task_id

    def get_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def get_result(self, task_id: str) -> Any:
        """获取任务结果"""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.COMPLETED:
            return task.result
        return None

    def process_next(self, registry: Dict[str, Callable]) -> bool:
        """处理队列中的下一个任务"""
        if not self._queue:
            return False

        task_id = self._queue.pop(0)
        task = self._tasks.get(task_id)
        if not task:
            return False

        func = registry.get(task.func_name)
        if not func:
            task.status = TaskStatus.FAILED
            task.error = f"Unknown function: {task.func_name}"
            task.completed_at = time.time()
            return True

        task.status = TaskStatus.PROCESSING
        task.started_at = time.time()

        try:
            result = func(*task.args, **task.kwargs)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            logger.exception("任务执行失败: %s", task_id)
        finally:
            task.completed_at = time.time()

        return True

    def queue_size(self) -> int:
        return len(self._queue)


class RedisQueue:
    """Redis 队列（生产用）"""

    def __init__(self, redis_url: str = None):
        import redis
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.client = redis.from_url(self.redis_url, decode_responses=True)
        self.prefix = "deepdoc:task:"
        # 测试连接是否可用
        try:
            self.client.ping()
        except Exception as e:
            raise ConnectionError(f"Redis 连接失败: {e}") from e
        logger.info("初始化 Redis 任务队列: %s", self.redis_url)

    def enqueue(self, func: Callable, args: tuple = (), kwargs: dict = None,
                task_id: str = None) -> str:
        """入队任务"""
        task_id = task_id or str(uuid.uuid4())
        task_data = {
            "task_id": task_id,
            "func_name": func.__name__,
            "args": list(args),
            "kwargs": kwargs or {},
            "status": TaskStatus.PENDING.value,
            "created_at": time.time(),
        }
        pipe = self.client.pipeline()
        pipe.set(f"{self.prefix}{task_id}", json.dumps(task_data))
        pipe.lpush(f"{self.prefix}queue", task_id)
        pipe.execute()
        logger.debug("任务入队 Redis: %s (%s)", task_id, func.__name__)
        return task_id

    def get_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        data = self.client.get(f"{self.prefix}{task_id}")
        return json.loads(data) if data else None

    def get_result(self, task_id: str) -> Any:
        """获取任务结果"""
        status = self.get_status(task_id)
        if status and status.get("status") == TaskStatus.COMPLETED.value:
            return status.get("result")
        return None

    def process_next(self, registry: Dict[str, Callable]) -> bool:
        """处理队列中的下一个任务"""
        task_id = self.client.rpop(f"{self.prefix}queue")
        if not task_id:
            return False

        data = self.client.get(f"{self.prefix}{task_id}")
        if not data:
            return False

        task_data = json.loads(data)
        func_name = task_data.get("func_name")
        func = registry.get(func_name)

        if not func:
            task_data["status"] = TaskStatus.FAILED.value
            task_data["error"] = f"Unknown function: {func_name}"
            task_data["completed_at"] = time.time()
            self.client.set(f"{self.prefix}{task_id}", json.dumps(task_data))
            return True

        task_data["status"] = TaskStatus.PROCESSING.value
        task_data["started_at"] = time.time()
        self.client.set(f"{self.prefix}{task_id}", json.dumps(task_data))

        try:
            args = task_data.get("args", [])
            kwargs = task_data.get("kwargs", {})
            result = func(*args, **kwargs)
            task_data["result"] = result
            task_data["status"] = TaskStatus.COMPLETED.value
        except Exception as e:
            task_data["error"] = str(e)
            task_data["status"] = TaskStatus.FAILED.value
            logger.exception("任务执行失败: %s", task_id)
        finally:
            task_data["completed_at"] = time.time()
            self.client.set(f"{self.prefix}{task_id}", json.dumps(task_data))

        return True

    def queue_size(self) -> int:
        return self.client.llen(f"{self.prefix}queue")


def get_queue(redis_url: str = None):
    """获取任务队列实例

    优先使用 Redis（如果可用），否则使用内存队列。
    可通过环境变量 QUEUE_BACKEND 强制选择：
      - "redis": 使用 Redis
      - "memory": 使用内存队列
    """
    backend = os.getenv("QUEUE_BACKEND", "").lower()

    if backend == "memory":
        return InMemoryQueue()

    if backend == "redis" or not backend:
        try:
            return RedisQueue(redis_url)
        except Exception as e:
            if backend == "redis":
                raise
            logger.warning("Redis 不可用，降级为内存队列: %s", e)
            return InMemoryQueue()

    raise ValueError(f"未知的队列后端: {backend}")
