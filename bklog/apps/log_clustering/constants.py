"""
Tencent is pleased to support the open source community by making BK-LOG 蓝鲸日志平台 available.
Copyright (C) 2021 THL A29 Limited, a Tencent company.  All rights reserved.
BK-LOG 蓝鲸日志平台 is licensed under the MIT License.
License for BK-LOG 蓝鲸日志平台:
--------------------------------------------------------------------
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
We undertake not to change the open source license (MIT license) applicable to the current version of
the project delivered to anyone in the future.
"""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from apps.utils import ChoicesEnum

DEFAULT_NEW_CLS_HOURS = 24

CONTENT_PATTERN_INDEX = 1
LATEST_PUBLISH_STATUS = "latest"
PATTERN_SIGNATURE_INDEX = 5
PATTERN_INDEX = 0
ORIGIN_LOG_INDEX = 3

HOUR_MINUTES = 60
PERCENTAGE_RATE = 100
MIN_COUNT = 0
DOUBLE_PERCENTAGE = 100
EX_MAX_SIZE = 10000
IS_NEW_PATTERN_PREFIX = "is_new_class"
AGGS_FIELD_PREFIX = "__dist"
NEW_CLASS_FIELD_PREFIX = "dist"

NEW_CLASS_SENSITIVITY_FIELD = "sensitivity"
NEW_CLASS_QUERY_FIELDS = ["signature"]
NEW_CLASS_QUERY_TIME_RANGE = "customized"

CLUSTERING_CONFIG_EXCLUDE = ["sample_set_id", "model_id"]
CLUSTERING_CONFIG_DEFAULT = "default_clustering_config"

DEFAULT_CLUSTERING_FIELDS = "log"
DEFAULT_IS_CASE_SENSITIVE = 0

SAMPLE_SET_SLEEP_TIMER = 15 * 60

DEFULT_FILTER_NOT_CLUSTERING_OPERATOR = "is not"

NOTICE_RECEIVER = "user"

#  查找策略page_size 设置
DEFAULT_PAGE = 1
MAX_STRATEGY_PAGE_SIZE = 100

DEFAULT_SCENARIO = "other_rt"
DEFAULT_LABEL = [_("日志平台日志聚类告警")]
DEFAULT_NOTIFY_RECEIVER_TYPE = "user"
DEFAULT_NOTICE_WAY = {"3": ["rtx"], "2": ["rtx"], "1": ["rtx"]}
DEFAULT_NO_DATA_CONFIG = {"level": 2, "continuous": 10, "is_enabled": False, "agg_dimension": []}
DEFAULT_EXPRESSION = "a"
DEFAULT_DATA_SOURCE_LABEL = "bk_log_search"
DEFAULT_DATA_SOURCE_LABEL_BKDATA = "bk_data"
DEFAULT_DATA_TYPE_LABEL = "log"
DEFAULT_DATA_TYPE_LABEL_BKDATA = "time_series"
DEFAULT_AGG_METHOD_BKDATA = "COUNT"
DEFAULT_AGG_INTERVAL = 60
DEFAULT_TIME_FIELD = "dtEventTimeStamp"
DEFAULT_ALGORITHMS = [
    {"type": "Threshold", "level": 2, "config": [[{"method": "gte", "threshold": 1}]], "unit_prefix": ""}
]
DEFAULT_CLUSTERING_ITEM_NAME = _("日志聚类新类(近24H)")
DEFAULT_METRIC = "event_time"

# 保存告警策略 v3部分参数
DEFAULT_AGG_METHOD = "SUM"
ITEM_NAME_CLUSTERING = "SUM(log_count)"
DEFAULT_METRIC_CLUSTERING = "log_count"
ALARM_INTERVAL_CLUSTERING = 7200
# 数量突增告警
AGG_DIMENSION_NORMAL = ["__dist_05"]
AGG_CONDITION_NORMAL = [
    {"key": "__dist_05", "dimension_name": "__dist_05", "value": [""], "method": "neq", "condition": "and"}
]
# 新类告警
AGG_DIMENSION = ["sensitivity", "signature"]
AGG_CONDITION = [
    {"key": "sensitivity", "dimension_name": "sensitivity", "value": ["__dist_05"], "method": "eq", "condition": "and"}
]
TRIGGER_CONFIG = {
    "count": 1,
    "check_window": 5,
    "uptime": {"calendars": [], "time_ranges": [{"start": "00:00", "end": "23:59"}]},
}

DETECTS = [
    {
        "level": 2,
        "expression": "",
        "trigger_config": TRIGGER_CONFIG,
        "recovery_config": {"check_window": 5},
        "connector": "and",
    }
]

DEFAULT_ALERT_NOTICE = [
    {
        "time_range": "00:00:00--23:59:00",
        "notify_config": [
            {"notice_ways": [{"name": "rtx"}], "level": 3},
            {"notice_ways": [{"name": "rtx"}], "level": 2},
            {"notice_ways": [{"name": "rtx"}], "level": 1},
        ],
    }
]

DEFAULT_ACTION_NOTICE = [
    {
        "time_range": "00:00:00--23:59:00",
        "notify_config": [
            {"notice_ways": [{"name": "rtx"}], "phase": 3},
            {"notice_ways": [{"name": "rtx"}], "phase": 2},
            {"notice_ways": [{"name": "rtx"}], "phase": 1},
        ],
    }
]

DEFAULT_MENTION_LIST = [{"id": "all", "display_name": "all", "type": "group"}]

DEFAULT_DETECTS = [
    {
        "level": 2,
        "expression": "",
        "trigger_config": {"count": 1, "check_window": 5},
        "recovery_config": {"check_window": 5},
        "connector": "and",
    }
]
DEFAULT_ACTION_TYPE = "notice"
DEFAULT_ACTION_CONFIG = {
    "alarm_start_time": "00:00:00",
    "alarm_end_time": "23:59:59",
    "alarm_interval": 1440,
    "send_recovery_alarm": False,
}
NOT_NEED_EDIT_NODES = ["format_signature"]

DEFAULT_PATTERN_MONITOR_MSG = """
{{content.level}}
{{content.begin_time}}
{{content.time}}
{{content.duration}}
{{content.target_type}}
{{content.data_source}}
{{content.current_value}}
{{content.biz}}
{{content.target}}
{{content.dimension}}
{{content.detail}}

**内容:** 智能模型检测到异常, 异常类型: {{alarm.bkm_info.alert_msg}}, 近 {{ (strategy.items[0].query_configs[0]["agg_interval"]\
 / 60) | int }} 分钟出现次数 ({{alarm.current_value | int }})
**负责人:** {{ json.loads(alarm.related_info)["owners"] or '无' }}
**备注:** {% if "remark_text" in json.loads(alarm.related_info) %}{{ json.loads(alarm.related_info)["remark_text"] }}\
【{{ json.loads(alarm.related_info)["remark_time"] }}】({{ json.loads(alarm.related_info)["remark_user"] }} ){% else %}\
 无 {% endif %}
**日志示例:** {{ json.loads(alarm.related_info)["log"] }}
[更多日志]({{ json.loads(alarm.related_info)["bklog_link"] }})
"""

PATTERN_MONITOR_MSG_BY_SWITCH = """
{{content.level}}
{{content.begin_time}}
{{content.time}}
{{content.duration}}
{{content.target_type}}
{{content.data_source}}
{{content.content}}
{{content.current_value}}
{{content.biz}}
{{content.target}}
{{content.dimension}}
{{content.detail}}
{{content.assign_detail}}
{{content.related_info}}
"""


class StrategiesType:
    NEW_CLS_strategy = "new_cls_strategy"
    NORMAL_STRATEGY = "normal_strategy"


class StrategiesAlarmLevelEnum(ChoicesEnum):
    CRITICAL = 1
    WARNING = 2
    REMIND = 3

    _choices_labels = (
        (CRITICAL, _lazy("致命")),
        (WARNING, _lazy("预警")),
        (REMIND, _lazy("提醒")),
    )


class YearOnYearEnum(ChoicesEnum):
    NOT = 0
    ONE_HOUR = 1
    TWO_HOUR = 2
    THREE_HOUR = 3
    SIX_HOUR = 6
    HALF_DAY = 12
    ONE_DAY = 24

    _choices_labels = (
        (NOT, _lazy("不比对")),
        (ONE_HOUR, _lazy("1小时前")),
        (TWO_HOUR, _lazy("2小时前")),
        (THREE_HOUR, _lazy("3小时前")),
        (SIX_HOUR, _lazy("6小时前")),
        (HALF_DAY, _lazy("12小时前")),
        (ONE_DAY, _lazy("24小时前")),
    )


class PatternEnum(ChoicesEnum):
    LEVEL_05 = "05"

    _choices_labels = ((LEVEL_05, "LEVEL_05"),)


class ActionEnum(ChoicesEnum):
    CREATE = "create"
    DELETE = "delete"

    @classmethod
    def get_choices(cls) -> tuple:
        return (
            cls.CREATE.value,
            cls.DELETE.value,
        )


# 日志聚类失败重试次数
MAX_FAILED_REQUEST_RETRY = 3


class SubscriptionTypeEnum(ChoicesEnum):
    EMAIL = "email"
    WECHAT = "wechat"

    _choices_labels = (
        (EMAIL, _("邮件")),
        (WECHAT, _("企业微信")),
    )


class YearOnYearChangeEnum(ChoicesEnum):
    ALL = "all"
    RISE = "rise"
    DECLINE = "decline"

    _choices_labels = (
        (ALL, _("所有")),
        (RISE, _("上升")),
        (DECLINE, _("下降")),
    )


class LogColShowTypeEnum(ChoicesEnum):
    PATTERN = "pattern"
    LOG = "log"

    _choices_labels = (
        (PATTERN, _("PATTERN模式")),
        (LOG, _("采样日志")),
    )


class FrequencyTypeEnum(ChoicesEnum):
    MINUTE = 1
    DAY = 2
    WEEK = 3


class RemarkConfigEnum(ChoicesEnum):
    ALL = "all"
    NO_REMARK = "no_remark"
    REMARKED = "remarked"

    _choices_labels = (
        (ALL, _("全部")),
        (NO_REMARK, _("未备注")),
        (REMARKED, _("已备注")),
    )


class OwnerConfigEnum(ChoicesEnum):
    ALL = "all"
    NO_OWNER = "no_owner"
    OWNER = "owner"

    _choices_labels = (
        (ALL, _("全部")),
        (NO_OWNER, _("未指定责任人")),
        (OWNER, _("指定责任人")),
    )


class RegexRuleTypeEnum(ChoicesEnum):
    CUSTOMIZE = "customize"
    TEMPLATE = "template"

    _choices_labels = (
        (CUSTOMIZE, _("自定义")),
        (TEMPLATE, _("模板")),
    )
