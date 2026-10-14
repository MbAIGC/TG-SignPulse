from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from backend.core.database import get_session_local
from backend.models.task import Task
from backend.services.tasks import run_task_once
from backend.utils.memory import trim_memory

scheduler: AsyncIOScheduler | None = None
logger = logging.getLogger("backend.scheduler")

# 正在执行（含在 range 窗口内等待）的签到 job：补执行必须避开它们，
# 否则「窗口内等待」期间的一次同步就会导致重复签到。
_active_sign_jobs: set[tuple[str, str, int]] = set()


def _retry_delay_seconds() -> int:
    """失败后重试延迟；<=0 表示关闭自动重试。"""
    try:
        return int(os.getenv("SIGN_TASK_RETRY_DELAY_SECONDS", "600"))
    except (TypeError, ValueError):
        return 600


def _sign_job_id(account_name: str, task_name: str, index: int) -> str:
    return f"sign-{account_name}-{task_name}#{index}"


def _sign_retry_job_id(account_name: str, task_name: str, index: int) -> str:
    return f"{_sign_job_id(account_name, task_name, index)}-retry"


def _sign_catchup_job_id(account_name: str, task_name: str, index: int) -> str:
    return f"{_sign_job_id(account_name, task_name, index)}-catchup"


def _now() -> datetime:
    from backend.core.config import get_settings

    return datetime.now(ZoneInfo(get_settings().timezone))


def _last_run_date(task_config: dict | None) -> str:
    """从任务的 last_run 元数据里取日期（YYYY-MM-DD）。"""
    if not isinstance(task_config, dict):
        return ""
    last_run = task_config.get("last_run")
    if isinstance(last_run, dict):
        raw = str(last_run.get("time") or "")
    else:
        raw = str(last_run or "")
    return raw[:10]


def _range_window(task_config: dict) -> tuple[datetime, datetime] | None:
    """返回今天的 range 时间窗口；跨天窗口按结束时间落在次日处理。"""
    start_str = str(task_config.get("range_start") or "").strip()
    end_str = str(task_config.get("range_end") or "").strip()
    if not start_str or not end_str:
        return None
    try:
        start_time = _parse_clock_time(start_str)
        end_time = _parse_clock_time(end_str)
    except ValueError:
        return None
    now = _now()
    start_dt = now.replace(
        hour=start_time.hour,
        minute=start_time.minute,
        second=start_time.second,
        microsecond=0,
    )
    end_dt = now.replace(
        hour=end_time.hour,
        minute=end_time.minute,
        second=end_time.second,
        microsecond=0,
    )
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def schedule_range_catchup(
    account_name: str,
    task_name: str,
    task_config: dict | None,
    index: int = 0,
    total: int = 1,
) -> None:
    """range 模式补执行：进程在窗口内启动、或任务在窗口中途创建时补跑一次。

    只有在「今天窗口已开始且未结束」「今天还没有成功记录」「没有正在等待
    执行的同名 job」时才注册，避免与 cron 触发的正常执行重复签到。
    """
    if scheduler is None or not isinstance(task_config, dict):
        return
    if str(task_config.get("execution_mode") or "") != "range":
        return

    window = _range_window(task_config)
    if window is None:
        return
    start_dt, end_dt = window
    now = _now()
    # 窗口未开始：交给 cron 正常触发；窗口已结束：今天不再补
    if now < start_dt or now >= end_dt:
        return
    # 今天已有执行记录
    if _last_run_date(task_config) == now.date().isoformat():
        return
    # 已有 job 正在等待（窗口内随机等待也算）
    if (account_name, task_name, index) in _active_sign_jobs:
        return

    job_id = _sign_catchup_job_id(account_name, task_name, index)
    if scheduler.get_job(job_id) is not None:
        return

    remaining = max((end_dt - now).total_seconds(), 1.0)
    delay = random.uniform(0, remaining)
    run_at = now + timedelta(seconds=delay)
    try:
        scheduler.add_job(
            _job_run_sign_task,
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            args=[account_name, task_name, index, total],
            kwargs={"is_catchup": True},
            replace_existing=True,
        )
        logger.info(
            "Scheduler: 任务 %s 处于随机时间段内且今日未执行，%.0f 秒后补执行一次",
            job_id,
            delay,
        )
    except Exception as e:
        logger.error("Scheduler: 注册任务 %s 补执行失败: %s", job_id, e)


def _schedule_task_retry(
    account_name: str,
    task_name: str,
    index: int,
    total: int,
    reason: str = "",
) -> None:
    """失败后注册一次性重试 job；重试任务本身失败后不再产生新的重试。"""
    if scheduler is None:
        return
    delay = _retry_delay_seconds()
    if delay <= 0:
        return
    job_id = _sign_retry_job_id(account_name, task_name, index)
    try:
        from backend.core.config import get_settings

        run_date = datetime.now(ZoneInfo(get_settings().timezone)) + timedelta(
            seconds=delay
        )
        scheduler.add_job(
            _job_run_sign_task,
            trigger=DateTrigger(run_date=run_date),
            id=job_id,
            args=[account_name, task_name, index, total],
            kwargs={"is_retry": True},
            replace_existing=True,
        )
        logger.warning(
            "Scheduler: 任务 %s 未成功（%s），%s 秒后重试一次",
            job_id,
            reason or "未知原因",
            delay,
        )
    except Exception as e:
        logger.error("Scheduler: 注册任务 %s 重试失败: %s", job_id, e)


def _parse_clock_time(value: str):
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid clock time: {value}")


def create_cron_trigger(cron_str: str) -> CronTrigger:
    """自动解析格式并创建 CronTrigger，支持 5位和6位 cron 表达式以及 HH:MM 或 HH:MM:SS"""
    if ":" in cron_str:
        parts = cron_str.split(":")
        try:
            if len(parts) == 2:
                hour, minute = parts
                cron_str = f"0 {int(minute)} {int(hour)} * * *"
            elif len(parts) == 3:
                hour, minute, second = parts
                cron_str = f"{int(second)} {int(minute)} {int(hour)} * * *"
        except ValueError:
            pass

    parts = cron_str.split()
    if len(parts) == 6:
        return CronTrigger(
            second=parts[0],
            minute=parts[1],
            hour=parts[2],
            day=parts[3],
            month=parts[4],
            day_of_week=parts[5],
        )
    return CronTrigger.from_crontab(cron_str)


def _normalize_daily_times(value) -> int:
    """每日签到次数：1-5，非法值回退为 1。"""
    try:
        return max(1, min(int(value), 5))
    except (TypeError, ValueError):
        return 1


def _clock_offset_cron(cron_str: str, index: int, total: int) -> str:
    """给 HH:MM(:SS) 时间加上 index/total 天的偏移，用于每日多次执行分散。"""
    if ":" not in cron_str:
        return cron_str
    t = _parse_clock_time(cron_str)
    offset_min = round(index * 1440 / total)
    total_min = t.hour * 60 + t.minute + offset_min
    hour = (total_min // 60) % 24
    minute = total_min % 60
    return f"{t.second} {minute} {hour} * * *"


async def _job_run_task(task_id: int) -> None:
    db: Session = get_session_local()()
    try:
        # 这里的查询是同步的，对于 SQLite 且任务量不大可以接受
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task or not task.enabled:
            return
        # run_task_once 将被改为 async
        await run_task_once(db, task)
    finally:
        db.close()
        trim_memory()


async def _job_run_sign_task(
    account_name: str,
    task_name: str,
    index: int = 0,
    total: int = 1,
    is_retry: bool = False,
    is_catchup: bool = False,
) -> None:
    """运行签到任务的 Job 包装器"""
    import asyncio
    import logging
    import random
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from backend.core.config import get_settings
    from backend.services.sign_tasks import get_sign_task_service

    logger = logging.getLogger("backend.scheduler")
    prefix = "[重试] " if is_retry else ("[补执行] " if is_catchup else "")
    job_key = (account_name, task_name, index)
    _active_sign_jobs.add(job_key)
    try:
        logger.info(
            f"Scheduler: {prefix}正在运行签到任务 {task_name} (账号: {account_name})"
        )

        # 获取任务配置，检查是否为随机时间段模式
        sign_task_service = get_sign_task_service()
        task_config = sign_task_service.get_task(task_name, account_name)
        # 重试/补执行不再随机等待：时间点在注册 job 时就已经确定
        if (
            task_config
            and task_config.get("execution_mode") == "range"
            and not is_retry
            and not is_catchup
        ):
            range_start_str = task_config.get("range_start")
            range_end_str = task_config.get("range_end")

            if range_start_str and range_end_str:
                try:
                    # 解析时间
                    start_time = _parse_clock_time(range_start_str)
                    end_time = _parse_clock_time(range_end_str)

                    # 转换为当前日期的 datetime
                    now = datetime.now(ZoneInfo(get_settings().timezone))
                    start_dt = now.replace(
                        hour=start_time.hour,
                        minute=start_time.minute,
                        second=start_time.second,
                        microsecond=0,
                    )
                    end_dt = now.replace(
                        hour=end_time.hour,
                        minute=end_time.minute,
                        second=end_time.second,
                        microsecond=0,
                    )

                    # 如果结束时间小于开始时间，假设是第二天（虽然CRON触发通常在开始时间，这里做个防御）
                    if end_dt < start_dt:
                        end_dt += timedelta(days=1)

                    # 计算总秒数，并按每日次数分段（第 index 段内随机）
                    total_seconds = (end_dt - start_dt).total_seconds()

                    if total_seconds > 0:
                        seg = total_seconds / max(total, 1)
                        delay_seconds = random.uniform(
                            index * seg,
                            min((index + 1) * seg, total_seconds),
                        )
                        logger.info(
                            f"Scheduler: 任务 {task_name} 设置为随机时间段模式 "
                            f"({range_start_str} - {range_end_str}) "
                            f"第 {index + 1}/{total} 次"
                        )
                        logger.info(
                            f"Scheduler: 将随机等待 {int(delay_seconds)} 秒 ({delay_seconds / 60:.2f} 分钟) 后执行"
                        )

                        await asyncio.sleep(delay_seconds)

                except Exception as e:
                    logger.error(f"Scheduler: 计算随机时间段延迟失败: {e}，将立即执行")

        # run_task_with_logs 是 async 的，我们使用它
        sign_task_service = get_sign_task_service()
        result = await sign_task_service.run_task_with_logs(
            account_name, task_name, source="scheduler"
        )
        if result.get("success"):
            logger.info(f"Scheduler: {prefix}任务 {task_name} 执行成功")
        else:
            logger.error(
                f"Scheduler: {prefix}任务 {task_name} 执行失败: {result.get('error')}"
            )
            if not is_retry and not result.get("account_invalid"):
                _schedule_task_retry(
                    account_name,
                    task_name,
                    index,
                    total,
                    str(result.get("error") or ""),
                )
    except Exception as e:
        logger.error(f"Scheduler: 运行签到任务 {task_name} 失败: {e}", exc_info=True)
        if not is_retry:
            _schedule_task_retry(account_name, task_name, index, total, str(e))
    finally:
        _active_sign_jobs.discard(job_key)
        trim_memory()


async def _job_health_check_accounts() -> None:
    """每日账号健康巡检：发现 session 失效立即按去重逻辑推送通知。"""
    from backend.services.sign_tasks import get_sign_task_service

    try:
        summary = await get_sign_task_service().health_check_accounts()
        logger.info(
            "账号巡检完成：检查 %s 个，失效 %s 个，跳过 %s 个",
            summary.get("checked", 0),
            len(summary.get("invalid") or []),
            len(summary.get("skipped") or []),
        )
    except Exception:
        logger.exception("账号巡检失败")


async def _job_maintenance() -> None:
    """每日维护任务：清理旧日志等"""
    db: Session = get_session_local()()
    try:
        from backend.services.sign_tasks import get_sign_task_service
        from backend.services.tasks import cleanup_old_logs

        # 清理数据库任务日志
        count = cleanup_old_logs(db, days=3)
        logger.info("Maintenance: 已清理 %s 条数据库任务日志", count)

        # 清理签到任务日志
        sign_service = get_sign_task_service()
        sign_service._cleanup_old_logs()

        # 清理内存中的过期状态
        sign_service._prune_stale_entries()
    finally:
        db.close()
        trim_memory()


async def sync_jobs() -> None:
    """
    Sync APScheduler jobs from DB tasks table and file-based sign tasks.
    """
    if scheduler is None:
        return

    from backend.services.sign_tasks import get_sign_task_service

    db: Session = get_session_local()()
    try:
        # 1. 同步数据库任务
        tasks = db.query(Task).filter(Task.enabled).all()
        existing_ids = {
            job.id
            for job in scheduler.get_jobs()
            if job.id.startswith("db-") or job.id.startswith("sign-")
        }
        # 一次性重试/补执行 job 由失败路径与 range 补执行动态注册，不能被这里的
        # “清理过期 job”误删；只有当它的基础 job 仍然存在（任务仍启用）时才保留。
        auxiliary_job_ids = {
            job_id
            for job_id in existing_ids
            if job_id.endswith("-retry") or job_id.endswith("-catchup")
        }
        desired_ids = set()

        for task in tasks:
            job_id = f"db-{task.id}"
            desired_ids.add(job_id)

            try:
                trigger = create_cron_trigger(task.cron)
                if job_id in existing_ids:
                    scheduler.reschedule_job(job_id, trigger=trigger)
                else:
                    scheduler.add_job(
                        _job_run_task,
                        trigger=trigger,
                        id=job_id,
                        args=[task.id],
                        replace_existing=True,
                    )
            except Exception as e:
                logger.error("Error scheduling DB task %s: %s", task.id, e)

        # 2. 同步签到任务 (SignTask)
        # 使用缓存的任务列表，减少 I/O
        sign_task_service = get_sign_task_service()
        # Expand wildcard tasks for newly added accounts
        sign_task_service._expand_wildcard_tasks()
        sign_tasks = sign_task_service.list_tasks(force_refresh=True)
        for st in sign_tasks:
            account_name = str(st.get("account_name") or "").strip()
            task_name = str(st.get("name") or "").strip()
            if not account_name or not task_name:
                logger.warning(
                    "Skip scheduling sign task with missing account/name: %s", st
                )
                continue

            daily_times = _normalize_daily_times(st.get("daily_times"))
            job_ids = [
                f"sign-{account_name}-{task_name}#{i}" for i in range(daily_times)
            ]

            # SignTask 目前默认都是启用的，或者根据 st['enabled']
            if not st.get("enabled", True) or st.get("execution_mode") == "listen":
                for job_id in job_ids:
                    if job_id in existing_ids:
                        scheduler.remove_job(job_id)
                continue

            for i in range(daily_times):
                job_id = job_ids[i]
                desired_ids.add(job_id)
                try:
                    base_cron = st["sign_at"]
                    if st.get("execution_mode") == "range" and st.get("range_start"):
                        base_cron = st["range_start"]
                    trigger_cron = base_cron
                    if st.get("execution_mode") != "range" and daily_times > 1:
                        trigger_cron = _clock_offset_cron(base_cron, i, daily_times)
                    trigger = create_cron_trigger(trigger_cron)

                    # 使用新的 job wrapper（replace_existing 会同时更新 trigger 与 args）
                    scheduler.add_job(
                        _job_run_sign_task,
                        trigger=trigger,
                        id=job_id,
                        args=[account_name, task_name, i, daily_times],
                        replace_existing=True,
                    )
                except Exception as e:
                    logger.error("Error scheduling sign task %s: %s", task_name, e)

            # range 模式：若进程在窗口内启动/重启，或任务在窗口中途创建，
            # 今天又还没有执行记录，就补执行一次（内部有去重判断）。
            # 注意：last_run 只记录最近一次执行，daily_times>1 时这里只保证
            # “今天至少跑一次”，无法逐个补齐缺失的第 N 次。
            try:
                schedule_range_catchup(
                    account_name,
                    task_name,
                    st,
                    index=0,
                    total=daily_times,
                )
            except Exception as e:
                logger.error("Error scheduling sign task catchup %s: %s", task_name, e)

        # 保留仍在等待执行的一次性 job（其基础 job 仍存在时才保留）
        for auxiliary_id in auxiliary_job_ids:
            base_id = auxiliary_id.rsplit("-", 1)[0]
            if base_id in desired_ids:
                desired_ids.add(auxiliary_id)

        # remove obsolete jobs
        for job_id in existing_ids - desired_ids:
            scheduler.remove_job(job_id)
    finally:
        db.close()


async def init_scheduler(sync_on_startup: bool = True) -> AsyncIOScheduler:
    global scheduler
    if scheduler is None:
        from backend.core.config import get_settings

        settings = get_settings()
        scheduler = AsyncIOScheduler(
            timezone=settings.timezone,
            job_defaults={
                "misfire_grace_time": 3600,  # 允许任务延迟 1 小时执行
                "coalesce": True,  # 合并积压的执行
                "max_instances": 10,  # 增加并发实例数，避免多账号任务相互阻塞
            },
        )
        scheduler.start()

        # 添加每日凌晨 3 点执行的维护任务
        scheduler.add_job(
            _job_maintenance,
            trigger=CronTrigger.from_crontab("0 3 * * *"),
            id="system-maintenance",
            replace_existing=True,
        )

        # 每日 9 点账号健康巡检
        scheduler.add_job(
            _job_health_check_accounts,
            trigger=CronTrigger.from_crontab("0 9 * * *"),
            id="system-health-check",
            replace_existing=True,
        )

        if sync_on_startup:
            await sync_jobs()
    return scheduler


def shutdown_scheduler() -> None:
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None


def add_or_update_sign_task_job(
    account_name: str,
    task_name: str,
    cron_expression: str,
    enabled: bool = True,
    daily_times: int = 1,
    execution_mode: str = "fixed",
) -> None:
    """动态添加或更新签到任务 Job"""
    global scheduler
    if not scheduler:
        return

    if not enabled:
        remove_sign_task_job(account_name, task_name)
        return

    daily_times = _normalize_daily_times(daily_times)
    for i in range(daily_times):
        job_id = f"sign-{account_name}-{task_name}#{i}"
        try:
            cron = cron_expression
            if execution_mode != "range" and daily_times > 1:
                cron = _clock_offset_cron(cron_expression, i, daily_times)
            trigger = create_cron_trigger(cron)

            # 总是使用 replace_existing=True 来覆盖旧的
            scheduler.add_job(
                _job_run_sign_task,
                trigger=trigger,
                id=job_id,
                args=[account_name, task_name, i, daily_times],
                replace_existing=True,
            )
            logger.info("Scheduler: 已添加/更新任务 %s -> %s", job_id, cron)
        except Exception as e:
            logger.error("Scheduler: 添加任务 %s 失败: %s", job_id, e)

    # 在 range 窗口中途创建/修改任务时立即补执行一次
    if execution_mode == "range":
        try:
            from backend.services.sign_tasks import get_sign_task_service

            task_config = get_sign_task_service().get_task(task_name, account_name)
            schedule_range_catchup(
                account_name,
                task_name,
                task_config,
                index=0,
                total=daily_times,
            )
        except Exception as e:
            logger.error("Scheduler: 任务 %s 补执行安排失败: %s", task_name, e)


def remove_sign_task_job(account_name: str, task_name: str) -> None:
    """动态移除签到任务 Job"""
    global scheduler
    if not scheduler:
        return

    prefix = f"sign-{account_name}-{task_name}"
    for job in scheduler.get_jobs():
        if job.id == prefix or job.id.startswith(prefix + "#"):
            try:
                scheduler.remove_job(job.id)
                logger.info("Scheduler: 已移除任务 %s", job.id)
            except Exception as e:
                logger.error("Scheduler: 移除任务 %s 失败: %s", job.id, e)
