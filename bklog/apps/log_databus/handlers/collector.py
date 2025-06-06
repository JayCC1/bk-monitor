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
"""

import base64
import copy
import datetime
import json
import re
from collections import defaultdict
from typing import Any

import arrow
import yaml
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from kubernetes import client
from rest_framework.exceptions import ErrorDetail, ValidationError

from apps.api import BcsApi, BkDataAccessApi, CCApi, NodeApi, TransferApi
from apps.api.modules.bk_node import BKNodeApi
from apps.constants import UserOperationActionEnum, UserOperationTypeEnum
from apps.decorators import user_operation_record
from apps.exceptions import ApiError, ApiRequestError, ApiResultError
from apps.feature_toggle.handlers.toggle import FeatureToggleObject
from apps.feature_toggle.plugins.constants import (
    BCS_COLLECTOR,
    BCS_DEPLOYMENT_TYPE,
    FEATURE_COLLECTOR_ITSM,
)
from apps.iam import Permission, ResourceEnum
from apps.log_bcs.handlers.bcs_handler import BcsHandler
from apps.log_databus.constants import (
    ADMIN_REQUEST_USER,
    BIZ_TOPO_INDEX,
    BK_SUPPLIER_ACCOUNT,
    BKDATA_DATA_REGION,
    BKDATA_DATA_SCENARIO,
    BKDATA_DATA_SCENARIO_ID,
    BKDATA_DATA_SENSITIVITY,
    BKDATA_DATA_SOURCE,
    BKDATA_DATA_SOURCE_TAGS,
    BKDATA_PERMISSION,
    BKDATA_TAGS,
    BULK_CLUSTER_INFOS_LIMIT,
    CACHE_KEY_CLUSTER_INFO,
    CC_HOST_FIELDS,
    CC_SCOPE_FIELDS,
    CHECK_TASK_READY_NOTE_FOUND_EXCEPTION_CODE,
    CONTAINER_CONFIGS_TO_YAML_EXCLUDE_FIELDS,
    DEFAULT_RETENTION,
    INTERNAL_TOPO_INDEX,
    META_DATA_ENCODING,
    NOT_FOUND_CODE,
    SEARCH_BIZ_INST_TOPO_LEVEL,
    STORAGE_CLUSTER_TYPE,
    ArchiveInstanceType,
    CmdbFieldType,
    CollectStatus,
    ContainerCollectorType,
    ContainerCollectStatus,
    Environment,
    EtlConfig,
    ETLProcessorChoices,
    LabelSelectorOperator,
    LogPluginInfo,
    RunStatus,
    TargetNodeTypeEnum,
    TopoType,
    WorkLoadType,
)
from apps.log_databus.exceptions import (
    AllNamespaceNotAllowedException,
    BCSApiException,
    BcsClusterIdNotValidException,
    CollectNotSuccess,
    CollectNotSuccessNotCanStart,
    CollectorActiveException,
    CollectorBkDataNameDuplicateException,
    CollectorConfigDataIdNotExistException,
    CollectorConfigNameDuplicateException,
    CollectorConfigNameENDuplicateException,
    CollectorConfigNotExistException,
    CollectorCreateOrUpdateSubscriptionException,
    CollectorIllegalIPException,
    CollectorResultTableIDDuplicateException,
    ContainerCollectConfigValidateYamlException,
    MissedNamespaceException,
    ModifyCollectorConfigException,
    NamespaceNotValidException,
    NodeNotAllowedException,
    PublicESClusterNotExistException,
    RegexInvalidException,
    RegexMatchException,
    ResultTableNotExistException,
    RuleCollectorException,
    SubscriptionInfoNotFoundException,
    VclusterNodeNotAllowedException,
)
from apps.log_databus.handlers.collector_scenario import CollectorScenario
from apps.log_databus.handlers.collector_scenario.custom_define import get_custom
from apps.log_databus.handlers.collector_scenario.utils import (
    convert_filters_to_collector_condition,
    deal_collector_scenario_param,
)
from apps.log_databus.handlers.etl_storage import EtlStorage
from apps.log_databus.handlers.storage import StorageHandler
from apps.log_databus.models import (
    ArchiveConfig,
    BcsRule,
    BcsStorageClusterConfig,
    CleanStash,
    CollectorConfig,
    CollectorPlugin,
    ContainerCollectorConfig,
    DataLinkConfig,
)
from apps.log_databus.serializers import ContainerCollectorYamlSerializer
from apps.log_databus.tasks.bkdata import async_create_bkdata_data_id
from apps.log_esquery.utils.es_route import EsRoute
from apps.log_measure.events import NOTIFY_EVENT
from apps.log_search.constants import (
    CMDB_HOST_SEARCH_FIELDS,
    CollectorScenarioEnum,
    CustomTypeEnum,
    GlobalCategoriesEnum,
    InnerTag,
)
from apps.log_search.handlers.biz import BizHandler
from apps.log_search.handlers.index_set import IndexSetHandler
from apps.log_search.models import (
    IndexSetTag,
    LogIndexSet,
    LogIndexSetData,
    Scenario,
    Space,
)
from apps.models import model_to_dict
from apps.utils.bcs import Bcs
from apps.utils.cache import caches_one_hour
from apps.utils.custom_report import BK_CUSTOM_REPORT, CONFIG_OTLP_FIELD
from apps.utils.db import array_chunk
from apps.utils.function import map_if
from apps.utils.local import get_local_param, get_request_username
from apps.utils.log import logger
from apps.utils.thread import MultiExecuteFunc
from apps.utils.time_handler import format_user_time_zone
from bkm_space.define import SpaceTypeEnum

COLLECTOR_RE = re.compile(r".*\d{6,8}$")


class CollectorHandler:
    data: CollectorConfig

    def __init__(self, collector_config_id=None):
        super().__init__()
        self.collector_config_id = collector_config_id
        self.data = None
        if collector_config_id:
            try:
                self.data = CollectorConfig.objects.get(collector_config_id=self.collector_config_id)
            except CollectorConfig.DoesNotExist:
                raise CollectorConfigNotExistException()

    def _multi_info_get(self, use_request=True):
        """
        并发查询所需的配置
        @param use_request:
        @return:
        """
        multi_execute_func = MultiExecuteFunc()
        if self.data.bk_data_id:
            multi_execute_func.append(
                "data_id_config",
                TransferApi.get_data_id,
                params={"bk_data_id": self.data.bk_data_id},
                use_request=use_request,
            )
        if self.data.table_id:
            multi_execute_func.append(
                "result_table_config",
                TransferApi.get_result_table,
                params={"table_id": self.data.table_id},
                use_request=use_request,
            )
            multi_execute_func.append(
                "result_table_storage",
                TransferApi.get_result_table_storage,
                params={"result_table_list": self.data.table_id, "storage_type": "elasticsearch"},
                use_request=use_request,
            )
        if self.data.subscription_id:
            multi_execute_func.append(
                "subscription_config",
                BKNodeApi.get_subscription_info,
                params={"subscription_id_list": [self.data.subscription_id], "bk_biz_id": self.data.bk_biz_id},
                use_request=use_request,
            )
        return multi_execute_func.run()

    RETRIEVE_CHAIN = [
        "set_itsm_info",
        "set_split_rule",
        "set_target",
        "set_default_field",
        "set_categorie_name",
        "complement_metadata_info",
        "complement_nodeman_info",
        "fields_is_empty",
        "deal_time",
        "add_container_configs",
        "encode_yaml_config",
    ]

    def encode_yaml_config(self, collector_config, context):
        """
        encode_yaml_config
        @param collector_config:
        @param context:
        @return:
        """
        if not collector_config["yaml_config"]:
            return collector_config
        collector_config["yaml_config"] = base64.b64encode(collector_config["yaml_config"].encode("utf-8"))
        return collector_config

    def add_container_configs(self, collector_config, context):
        """
        add_container_configs
        @param collector_config:
        @param context:
        @return:
        """
        if not self.data.is_container_environment:
            return collector_config

        container_configs = []
        for config in ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id):
            container_configs.append(model_to_dict(config))

        collector_config["configs"] = container_configs
        return collector_config

    def set_itsm_info(self, collector_config, context):  # noqa
        """
        set_itsm_info
        @param collector_config:
        @param context:
        @return:
        """
        from apps.log_databus.handlers.itsm import ItsmHandler

        itsm_info = ItsmHandler().collect_itsm_status(collect_config_id=collector_config["collector_config_id"])
        collector_config.update(
            {
                "iframe_ticket_url": itsm_info["iframe_ticket_url"],
                "ticket_url": itsm_info["ticket_url"],
                "itsm_ticket_status": itsm_info["collect_itsm_status"],
                "itsm_ticket_status_display": itsm_info["collect_itsm_status_display"],
            }
        )
        return collector_config

    def set_default_field(self, collector_config, context):  # noqa
        """
        set_default_field
        @param collector_config:
        @param context:
        @return:
        """
        collector_config.update(
            {
                "collector_scenario_name": self.data.get_collector_scenario_id_display(),
                "bk_data_name": self.data.bk_data_name,
                "storage_cluster_id": None,
                "retention": None,
                "etl_params": {},
                "fields": [],
            }
        )
        return collector_config

    def set_split_rule(self, collector_config, context):  # noqa
        """
        set_split_rule
        @param collector_config:
        @param context:
        @return:
        """
        collector_config["index_split_rule"] = "--"
        if self.data.table_id and collector_config["storage_shards_size"]:
            slice_size = collector_config["storage_shards_nums"] * collector_config["storage_shards_size"]
            collector_config["index_split_rule"] = _("ES索引主分片大小达到{}G后分裂").format(slice_size)
        return collector_config

    def set_target(self, collector_config: dict, context):  # noqa
        """
        set_target
        @param collector_config:
        @param context:
        @return:
        """
        if collector_config["target_node_type"] == "INSTANCE":
            collector_config["target"] = collector_config.get("target_nodes", [])
            return collector_config
        nodes = collector_config.get("target_nodes", [])
        bk_module_inst_ids = self._get_ids("module", nodes)
        bk_set_inst_ids = self._get_ids("set", nodes)
        collector_config["target"] = []
        biz_handler = BizHandler(bk_biz_id=collector_config["bk_biz_id"])
        result_module = biz_handler.get_modules_info(bk_module_inst_ids)
        result_set = biz_handler.get_sets_info(bk_set_inst_ids)
        collector_config["target"].extend(result_module)
        collector_config["target"].extend(result_set)
        return collector_config

    def set_categorie_name(self, collector_config, context):
        """
        set_target
        @param collector_config:
        @param context:
        @return:
        """
        collector_config["category_name"] = GlobalCategoriesEnum.get_display(collector_config["category_id"])
        collector_config["custom_name"] = CustomTypeEnum.get_choice_label(collector_config["custom_type"])
        return collector_config

    def complement_metadata_info(self, collector_config, context):
        """
        补全保存在metadata 结果表中的配置
        @param collector_config:
        @param context:
        @return:
        """
        result = context
        if not self.data.table_id:
            collector_config.update({"table_id_prefix": build_bk_table_id(self.data.bk_biz_id, ""), "table_id": ""})
            return collector_config
        table_id_prefix, table_id = self.data.table_id.split(".")
        collector_config.update({"table_id_prefix": table_id_prefix + "_", "table_id": table_id})

        if "result_table_config" in result and "result_table_storage" in result:
            if self.data.table_id in result["result_table_storage"]:
                self.data.etl_config = EtlStorage.get_etl_config(
                    result["result_table_config"], default=self.data.etl_config
                )
                etl_storage = EtlStorage.get_instance(etl_config=self.data.etl_config)
                collector_config.update(
                    etl_storage.parse_result_table_config(
                        result_table_config=result["result_table_config"],
                        result_table_storage=result["result_table_storage"][self.data.table_id],
                        fields_dict=self.get_fields_dict(self.data.collector_config_id),
                    )
                )
                # 补充es集群端口号 、es集群域名
                storage_cluster_id = collector_config.get("storage_cluster_id", "")
                cluster_config = IndexSetHandler.get_cluster_map().get(storage_cluster_id, {})
                collector_config.update(
                    {
                        "storage_cluster_port": cluster_config.get("cluster_port", ""),
                        "storage_cluster_domain_name": cluster_config.get("cluster_domain_name", ""),
                    }
                )
            return collector_config
        return collector_config

    def complement_nodeman_info(self, collector_config, context):
        """
        补全保存在节点管理的订阅配置
        @param collector_config:
        @param context:
        @return:
        """
        result = context
        if self.data.subscription_id and "subscription_config" in result:
            if not result["subscription_config"]:
                raise SubscriptionInfoNotFoundException()
            subscription_config = result["subscription_config"][0]
            collector_scenario = CollectorScenario.get_instance(collector_scenario_id=self.data.collector_scenario_id)
            params = collector_scenario.parse_steps(subscription_config["steps"])
            collector_config.update({"params": params})
            data_encoding = params.get("encoding")
            if data_encoding:
                # 将对应data_encoding 转换成大写供前端
                collector_config.update({"data_encoding": data_encoding.upper()})
        return collector_config

    def fields_is_empty(self, collector_config, context):  # noqa
        """
        如果数据未入库，则fields为空，直接使用默认标准字段返回
        @param collector_config:
        @param context:
        @return:
        """
        if not collector_config["fields"]:
            etl_storage = EtlStorage.get_instance(EtlConfig.BK_LOG_TEXT)
            collector_scenario = CollectorScenario.get_instance(collector_scenario_id=self.data.collector_scenario_id)
            built_in_config = collector_scenario.get_built_in_config()
            result_table_config = etl_storage.get_result_table_config(
                fields=None, etl_params=None, built_in_config=built_in_config
            )
            etl_config = etl_storage.parse_result_table_config(result_table_config)
            collector_config["fields"] = etl_config.get("fields", [])
        return collector_config

    def deal_time(self, collector_config, context):  # noqa
        """
        对 collector_config进行时区转换
        @param collector_config:
        @param context:
        @return:
        """
        time_zone = get_local_param("time_zone", settings.TIME_ZONE)
        collector_config["updated_at"] = format_user_time_zone(collector_config["updated_at"], time_zone=time_zone)
        collector_config["created_at"] = format_user_time_zone(collector_config["created_at"], time_zone=time_zone)
        return collector_config

    def retrieve(self, use_request=True):
        """
        获取采集配置
        @param use_request:
        @return:
        """
        context = self._multi_info_get(use_request)
        collector_config = model_to_dict(self.data)
        for process in self.RETRIEVE_CHAIN:
            collector_config = getattr(self, process, lambda x, y: x)(collector_config, context)
            logger.info(f"[databus retrieve] process => [{process}] collector_config => [{collector_config}]")
        if self.data.table_id:
            result_table = TransferApi.get_result_table({"table_id": self.data.table_id})
            alias_dict = result_table.get("query_alias_settings", dict())
            if alias_dict:
                collector_config.update({"alias_settings": alias_dict})

        # 添加索引集相关信息
        log_index_set_obj = LogIndexSet.objects.filter(collector_config_id=self.collector_config_id).first()
        if log_index_set_obj:
            collector_config.update(
                {"sort_fields": log_index_set_obj.sort_fields, "target_fields": log_index_set_obj.target_fields}
            )

        return collector_config

    @staticmethod
    def get_fields_dict(collector_config_id: int):
        """
        获取字段的自定义分词和是否大小写信息
        """
        fields_dict = {}
        clean_stash = CleanStash.objects.filter(collector_config_id=collector_config_id).first()
        if not clean_stash:
            return fields_dict
        etl_params = clean_stash.etl_params or {}
        fields_dict = {
            "log": {
                "is_case_sensitive": etl_params.get("original_text_is_case_sensitive", False),
                "tokenize_on_chars": etl_params.get("original_text_tokenize_on_chars", ""),
            }
        }
        etl_fields = clean_stash.etl_fields or []
        for etl_field in etl_fields:
            fields_dict.update(
                {
                    etl_field["field_name"]: {
                        "is_case_sensitive": etl_field.get("is_case_sensitive", False),
                        "tokenize_on_chars": etl_field.get("tokenize_on_chars", ""),
                    }
                }
            )
        return fields_dict

    def get_report_token(self):
        """
        获取上报Token
        """
        data = {"bk_data_token": ""}
        if self.data.custom_type == CustomTypeEnum.OTLP_LOG.value and self.data.log_group_id:
            log_group = TransferApi.get_log_group({"log_group_id": self.data.log_group_id})
            data["bk_data_token"] = log_group.get("bk_data_token", "")
        return data

    def get_report_host(self):
        """
        获取上报Host
        """

        data = {}
        bk_custom_report = FeatureToggleObject.toggle(BK_CUSTOM_REPORT)
        if bk_custom_report:
            data = bk_custom_report.feature_config.get(CONFIG_OTLP_FIELD, {})
        return data

    def _get_ids(self, node_type: str, nodes: list):
        return [node["bk_inst_id"] for node in nodes if node["bk_obj_id"] == node_type]

    @staticmethod
    @caches_one_hour(key=CACHE_KEY_CLUSTER_INFO, need_deconstruction_name="result_table_list")
    def bulk_cluster_infos(result_table_list: list):
        """
        bulk_cluster_infos
        @param result_table_list:
        @return:
        """
        multi_execute_func = MultiExecuteFunc()
        table_chunk = array_chunk(result_table_list, BULK_CLUSTER_INFOS_LIMIT)
        for item in table_chunk:
            rt = ",".join(item)
            multi_execute_func.append(
                rt, TransferApi.get_result_table_storage, {"result_table_list": rt, "storage_type": "elasticsearch"}
            )
        result = multi_execute_func.run()
        cluster_infos = {}
        for _, cluster_info in result.items():  # noqa
            cluster_infos.update(cluster_info)
        return cluster_infos

    @classmethod
    def add_cluster_info(cls, data):
        """
        补充集群信息
        @param data:
        @return:
        """
        result_table_list = [_data["table_id"] for _data in data if _data.get("table_id")]

        try:
            cluster_infos = cls.bulk_cluster_infos(result_table_list=result_table_list)
        except ApiError as error:
            logger.exception(f"request cluster info error => [{error}]")
            cluster_infos = {}

        time_zone = get_local_param("time_zone")
        for _data in data:
            cluster_info = cluster_infos.get(
                _data["table_id"],
                {"cluster_config": {"cluster_id": -1, "cluster_name": ""}, "storage_config": {"retention": 0}},
            )
            _data["storage_cluster_id"] = cluster_info["cluster_config"]["cluster_id"]
            _data["storage_cluster_name"] = cluster_info["cluster_config"]["cluster_name"]
            _data["retention"] = cluster_info["storage_config"]["retention"]
            # table_id
            if _data.get("table_id"):
                table_id_prefix, table_id = _data["table_id"].split(".")
                _data["table_id_prefix"] = table_id_prefix + "_"
                _data["table_id"] = table_id
            # 分类名
            _data["category_name"] = GlobalCategoriesEnum.get_display(_data["category_id"])
            _data["custom_name"] = CustomTypeEnum.get_choice_label(_data["custom_type"])

            # 时间处理
            _data["created_at"] = (
                arrow.get(_data["created_at"])
                .replace(tzinfo=settings.TIME_ZONE)
                .to(time_zone)
                .strftime(settings.BKDATA_DATETIME_FORMAT)
            )
            _data["updated_at"] = (
                arrow.get(_data["updated_at"])
                .replace(tzinfo=settings.TIME_ZONE)
                .to(time_zone)
                .strftime(settings.BKDATA_DATETIME_FORMAT)
            )

            # 是否可以检索
            if _data["is_active"] and _data["index_set_id"]:
                _data["is_search"] = (
                    not LogIndexSetData.objects.filter(index_set_id=_data["index_set_id"])
                    .exclude(apply_status="normal")
                    .exists()
                )
            else:
                _data["is_search"] = False

        return data

    @classmethod
    def add_tags_info(cls, data):
        """添加标签信息"""
        index_set_ids = [data_info.get("index_set_id") for data_info in data if data_info.get("index_set_id")]
        index_set_objs = LogIndexSet.origin_objects.filter(index_set_id__in=index_set_ids)

        tag_ids_mapping = dict()
        tag_ids_all = list()

        for obj in index_set_objs:
            tag_ids_mapping[obj.index_set_id] = obj.tag_ids
            tag_ids_all.extend(obj.tag_ids)

        # 查询出所有的tag信息
        index_set_tag_objs = IndexSetTag.objects.filter(tag_id__in=tag_ids_all)
        index_set_tag_mapping = {
            obj.tag_id: {
                "name": InnerTag.get_choice_label(obj.name),
                "color": obj.color,
                "tag_id": obj.tag_id,
            }
            for obj in index_set_tag_objs
        }

        for data_info in data:
            index_set_id = data_info.get("index_set_id", None)
            if not index_set_id:
                data_info["tags"] = list()
                continue
            tag_ids = tag_ids_mapping.get(int(index_set_id), [])
            if not tag_ids:
                data_info["tags"] = list()
                continue
            data_info["tags"] = [
                index_set_tag_mapping.get(int(tag_id)) for tag_id in tag_ids if index_set_tag_mapping.get(int(tag_id))
            ]

        return data

    @transaction.atomic
    def only_create_or_update_model(self, params):
        """
        only_create_or_update_model
        @param params:
        @return:
        """
        if self.data and not self.data.is_active:
            raise CollectorActiveException()
        model_fields = {
            "collector_config_name": params["collector_config_name"],
            "collector_config_name_en": params["collector_config_name_en"],
            "target_object_type": params["target_object_type"],
            "target_node_type": params["target_node_type"],
            "target_nodes": params["target_nodes"],
            "description": params.get("description") or params["collector_config_name"],
            "is_active": True,
            "data_encoding": params["data_encoding"],
            "params": params["params"],
            "environment": params["environment"],
            "extra_labels": params["params"].get("extra_labels", []),
        }

        bk_biz_id = params.get("bk_biz_id") or self.data.bk_biz_id
        collector_config_name_en = params["collector_config_name_en"]

        # 判断是否存在非法IP列表
        self.cat_illegal_ips(params)
        # 判断是否已存在同英文名collector
        if self._pre_check_collector_config_en(model_fields=model_fields, bk_biz_id=bk_biz_id):
            logger.error(
                "collector_config_name_en {collector_config_name_en} already exists".format(
                    collector_config_name_en=collector_config_name_en
                )
            )
            raise CollectorConfigNameENDuplicateException(
                CollectorConfigNameENDuplicateException.MESSAGE.format(
                    collector_config_name_en=collector_config_name_en
                )
            )
        # 判断是否已存在同bk_data_name, result_table_id
        bk_data_name = build_bk_data_name(bk_biz_id=bk_biz_id, collector_config_name_en=collector_config_name_en)
        result_table_id = build_result_table_id(bk_biz_id=bk_biz_id, collector_config_name_en=collector_config_name_en)
        if self._pre_check_bk_data_name(model_fields=model_fields, bk_data_name=bk_data_name):
            logger.error(f"bk_data_name {bk_data_name} already exists")
            raise CollectorBkDataNameDuplicateException(
                CollectorBkDataNameDuplicateException.MESSAGE.format(bk_data_name=bk_data_name)
            )
        if self._pre_check_result_table_id(model_fields=model_fields, result_table_id=result_table_id):
            logger.error(f"result_table_id {result_table_id} already exists")
            raise CollectorResultTableIDDuplicateException(
                CollectorResultTableIDDuplicateException.MESSAGE.format(result_table_id=result_table_id)
            )
        is_create = False
        try:
            if not self.data:
                model_fields.update(
                    {
                        "category_id": params["category_id"],
                        "collector_scenario_id": params["collector_scenario_id"],
                        "bk_biz_id": bk_biz_id,
                        "bkdata_biz_id": params.get("bkdata_biz_id"),
                        "data_link_id": int(params["data_link_id"]) if params.get("data_link_id") else 0,
                        "bk_data_id": params.get("bk_data_id"),
                        "etl_processor": params.get("etl_processor", ETLProcessorChoices.TRANSFER.value),
                        "etl_config": params.get("etl_config"),
                        "collector_plugin_id": params.get("collector_plugin_id"),
                    }
                )
                self.data = CollectorConfig.objects.create(**model_fields)
                is_create = True
            else:
                _collector_config_name = self.data.collector_config_name
                if self.data.bk_data_id and self.data.bk_data_name != bk_data_name:
                    TransferApi.modify_data_id({"data_id": self.data.bk_data_id, "data_name": bk_data_name})
                    logger.info(
                        "[modify_data_name] bk_data_id=>{}, data_name {}=>{}".format(
                            self.data.bk_data_id, self.data.bk_data_name, bk_data_name
                        )
                    )
                    self.data.bk_data_name = bk_data_name

                # 当更新itsm流程时 将diff更新前移
                if not FeatureToggleObject.switch(name=FEATURE_COLLECTOR_ITSM):
                    self.data.target_subscription_diff = self.diff_target_nodes(params["target_nodes"])
                for key, value in model_fields.items():
                    setattr(self.data, key, value)
                self.data.save()

                # collector_config_name更改后更新索引集名称
                if _collector_config_name != self.data.collector_config_name and self.data.index_set_id:
                    index_set_name = _("[采集项]") + self.data.collector_config_name
                    LogIndexSet.objects.filter(index_set_id=self.data.index_set_id).update(
                        index_set_name=index_set_name
                    )

            if params.get("is_allow_alone_data_id", True):
                if self.data.etl_processor == ETLProcessorChoices.BKBASE.value:
                    transfer_data_id = self.update_or_create_data_id(
                        self.data, etl_processor=ETLProcessorChoices.TRANSFER.value
                    )
                    self.data.bk_data_id = self.update_or_create_data_id(self.data, bk_data_id=transfer_data_id)
                else:
                    self.data.bk_data_id = self.update_or_create_data_id(self.data)
                self.data.save()

        except IntegrityError:
            logger.warning(f"collector config name duplicate => [{params['collector_config_name']}]")
            raise CollectorConfigNameDuplicateException()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.CREATE if is_create else UserOperationActionEnum.UPDATE,
            "params": params,
        }
        user_operation_record.delay(operation_record)

        if is_create:
            self._authorization_collector(self.data)

        return model_to_dict(self.data)

    @classmethod
    def update_or_create_data_id(
        cls, instance: CollectorConfig | CollectorPlugin, etl_processor: str = None, bk_data_id: int = None
    ) -> int:
        """
        创建或更新数据源
        @param instance:
        @param etl_processor:
        @param bk_data_id:
        @return:
        """

        if etl_processor is None:
            etl_processor = instance.etl_processor

        # 创建 Transfer
        if etl_processor == ETLProcessorChoices.TRANSFER.value:
            collector_scenario = CollectorScenario.get_instance(instance.collector_scenario_id)
            bk_data_id = collector_scenario.update_or_create_data_id(
                bk_data_id=instance.bk_data_id,
                data_link_id=instance.data_link_id,
                data_name=build_bk_data_name(instance.get_bk_biz_id(), instance.get_en_name()),
                description=instance.description,
                encoding=META_DATA_ENCODING,
            )
            return bk_data_id

        # 兼容平台账户
        bk_username = getattr(instance, "__platform_username", None) or instance.get_updated_by()

        # 创建 BKBase
        maintainers = {bk_username} if bk_username else {instance.updated_by, instance.created_by}

        if ADMIN_REQUEST_USER in maintainers and len(maintainers) > 1:
            maintainers.discard(ADMIN_REQUEST_USER)

        bkdata_params = {
            "operator": bk_username,
            "bk_username": bk_username,
            "data_scenario": BKDATA_DATA_SCENARIO,
            "data_scenario_id": BKDATA_DATA_SCENARIO_ID,
            "permission": BKDATA_PERMISSION,
            "bk_biz_id": instance.get_bk_biz_id(),
            "description": instance.description,
            "access_raw_data": {
                "tags": BKDATA_TAGS,
                "raw_data_name": instance.get_en_name(),
                "maintainer": ",".join(maintainers),
                "raw_data_alias": instance.get_en_name(),
                "data_source_tags": BKDATA_DATA_SOURCE_TAGS,
                "data_region": BKDATA_DATA_REGION,
                "data_source": BKDATA_DATA_SOURCE,
                "data_encoding": (instance.data_encoding if instance.data_encoding else META_DATA_ENCODING),
                "sensitivity": BKDATA_DATA_SENSITIVITY,
                "description": instance.description,
            },
        }

        if bk_data_id and not instance.bk_data_id:
            bkdata_params["access_raw_data"]["preassigned_data_id"] = bk_data_id

        # 更新
        if instance.bk_data_id:
            bkdata_params["access_raw_data"].update({"preassigned_data_id": instance.bk_data_id})
            bkdata_params.update({"raw_data_id": instance.bk_data_id})
            BkDataAccessApi.deploy_plan_put(bkdata_params)
            return instance.bk_data_id

        # 创建
        result = BkDataAccessApi.deploy_plan_post(bkdata_params)
        return result["raw_data_id"]

    def update_or_create(self, params: dict) -> dict:
        """
        创建采集配置
        :return:
        {
            "collector_config_id": 1,
            "collector_config_name": "采集项名称",
            "bk_data_id": 2001,
            "subscription_id": 1,
            "task_id_list": [1]
        }
        """
        if self.data and not self.data.is_active:
            raise CollectorActiveException()
        collector_config_name = params["collector_config_name"]
        collector_config_name_en = params["collector_config_name_en"]
        target_object_type = params["target_object_type"]
        target_node_type = params["target_node_type"]
        target_nodes = params["target_nodes"]
        data_encoding = params["data_encoding"]
        description = params.get("description") or collector_config_name
        bk_biz_id = params.get("bk_biz_id") or self.data.bk_biz_id
        is_display = params.get("is_display", True)
        params["params"]["encoding"] = data_encoding
        params["params"]["run_task"] = params.get("run_task", True)

        # cmdb元数据补充
        extra_labels = params["params"].get("extra_labels")
        if extra_labels:
            for item in extra_labels:
                if item["value"] == CmdbFieldType.HOST.value and item["key"] in CC_HOST_FIELDS:
                    item["value"] = "{{cmdb_instance." + item["value"] + "." + item["key"] + "}}"
                    item["key"] = "host.{}".format(item["key"])
                if item["value"] == CmdbFieldType.SCOPE.value and item["key"] in CC_SCOPE_FIELDS:
                    item["value"] = "{{cmdb_instance.host.relations[0]." + item["key"] + "}}"
                    item["key"] = "host.{}".format(item["key"])

        # 1. 创建CollectorConfig记录
        model_fields = {
            "collector_config_name": collector_config_name,
            "collector_config_name_en": collector_config_name_en,
            "target_object_type": target_object_type,
            "target_node_type": target_node_type,
            "target_nodes": target_nodes,
            "description": description,
            "data_encoding": data_encoding,
            "params": params["params"],
            "is_active": True,
            "is_display": is_display,
            "extra_labels": params["params"].get("extra_labels", []),
        }

        if "environment" in params:
            # 如果传了 environment 就设置，不传就不设置
            model_fields["environment"] = params["environment"]

        # 判断是否存在非法IP列表
        self.cat_illegal_ips(params)

        is_create = False

        # 判断是否已存在同英文名collector
        if self._pre_check_collector_config_en(model_fields=model_fields, bk_biz_id=bk_biz_id):
            logger.error(
                "collector_config_name_en {collector_config_name_en} already exists".format(
                    collector_config_name_en=collector_config_name_en
                )
            )
            raise CollectorConfigNameENDuplicateException(
                CollectorConfigNameENDuplicateException.MESSAGE.format(
                    collector_config_name_en=collector_config_name_en
                )
            )
        # 判断是否已存在同bk_data_name, result_table_id
        bkdata_biz_id = params.get("bkdata_biz_id") or bk_biz_id
        bk_data_name = build_bk_data_name(bk_biz_id=bkdata_biz_id, collector_config_name_en=collector_config_name_en)
        result_table_id = build_result_table_id(
            bk_biz_id=bkdata_biz_id, collector_config_name_en=collector_config_name_en
        )
        if self._pre_check_bk_data_name(model_fields=model_fields, bk_data_name=bk_data_name):
            logger.error(f"bk_data_name {bk_data_name} already exists")
            raise CollectorBkDataNameDuplicateException(
                CollectorBkDataNameDuplicateException.MESSAGE.format(bk_data_name=bk_data_name)
            )
        if self._pre_check_result_table_id(model_fields=model_fields, result_table_id=result_table_id):
            logger.error(f"result_table_id {result_table_id} already exists")
            raise CollectorResultTableIDDuplicateException(
                CollectorResultTableIDDuplicateException.MESSAGE.format(result_table_id=result_table_id)
            )
        # 2. 创建/更新采集项，并同步到bk_data_id
        with transaction.atomic():
            try:
                # 2.1 创建/更新采集项
                if not self.data:
                    data_link_id = int(params.get("data_link_id") or 0)
                    # 创建后不允许修改的字段
                    model_fields.update(
                        {
                            "category_id": params["category_id"],
                            "collector_scenario_id": params["collector_scenario_id"],
                            "bk_biz_id": bk_biz_id,
                            "bkdata_biz_id": params.get("bkdata_biz_id"),
                            "data_link_id": get_data_link_id(bk_biz_id=bk_biz_id, data_link_id=data_link_id),
                            "bk_data_id": params.get("bk_data_id"),
                            "etl_processor": params.get("etl_processor", ETLProcessorChoices.TRANSFER.value),
                            "etl_config": params.get("etl_config"),
                            "collector_plugin_id": params.get("collector_plugin_id"),
                        }
                    )
                    model_fields["collector_scenario_id"] = params["collector_scenario_id"]
                    self.data = CollectorConfig.objects.create(**model_fields)
                    is_create = True
                else:
                    _collector_config_name = copy.deepcopy(self.data.collector_config_name)
                    if self.data.bk_data_id and self.data.bk_data_name != bk_data_name:
                        TransferApi.modify_data_id({"data_id": self.data.bk_data_id, "data_name": bk_data_name})
                        logger.info(
                            "[modify_data_name] bk_data_id=>{}, data_name {}=>{}".format(
                                self.data.bk_data_id, self.data.bk_data_name, bk_data_name
                            )
                        )
                        self.data.bk_data_name = bk_data_name

                    # 当更新itsm流程时 将diff更新前移
                    if not FeatureToggleObject.switch(name=FEATURE_COLLECTOR_ITSM):
                        self.data.target_subscription_diff = self.diff_target_nodes(target_nodes)

                    if "collector_scenario_id" in params:
                        model_fields["collector_scenario_id"] = params["collector_scenario_id"]

                    for key, value in model_fields.items():
                        setattr(self.data, key, value)
                    self.data.save()

                    # collector_config_name更改后更新索引集名称
                    if _collector_config_name != self.data.collector_config_name and self.data.index_set_id:
                        index_set_name = _("[采集项]") + self.data.collector_config_name
                        LogIndexSet.objects.filter(index_set_id=self.data.index_set_id).update(
                            index_set_name=index_set_name
                        )

                # 2.2 meta-创建或更新数据源
                if params.get("is_allow_alone_data_id", True):
                    if self.data.etl_processor == ETLProcessorChoices.BKBASE.value:
                        # 兼容平台账号
                        if params.get("platform_username"):
                            setattr(self.data, "__platform_username", params["platform_username"])
                        # 创建
                        transfer_data_id = self.update_or_create_data_id(
                            self.data, etl_processor=ETLProcessorChoices.TRANSFER.value
                        )
                        self.data.bk_data_id = self.update_or_create_data_id(self.data, bk_data_id=transfer_data_id)
                    else:
                        self.data.bk_data_id = self.update_or_create_data_id(self.data)
                    self.data.save()

            except IntegrityError:
                logger.warning(f"collector config name duplicate => [{collector_config_name}]")
                raise CollectorConfigNameDuplicateException()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.CREATE if is_create else UserOperationActionEnum.UPDATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

        if is_create:
            self._authorization_collector(self.data)
            self.send_create_notify(self.data)
        try:
            collector_scenario = CollectorScenario.get_instance(self.data.collector_scenario_id)
            self._update_or_create_subscription(
                collector_scenario=collector_scenario, params=params["params"], is_create=is_create
            )
        finally:
            if (
                params.get("is_allow_alone_data_id", True)
                and params.get("etl_processor") != ETLProcessorChoices.BKBASE.value
            ):
                # 创建数据平台data_id
                async_create_bkdata_data_id.delay(self.data.collector_config_id)

        return {
            "collector_config_id": self.data.collector_config_id,
            "collector_config_name": self.data.collector_config_name,
            "bk_data_id": self.data.bk_data_id,
            "subscription_id": self.data.subscription_id,
            "task_id_list": self.data.task_id_list,
        }

    def _pre_check_collector_config_en(self, model_fields: dict, bk_biz_id: int):
        qs = CollectorConfig.objects.filter(
            collector_config_name_en=model_fields["collector_config_name_en"], bk_biz_id=bk_biz_id
        )
        if self.collector_config_id:
            qs = qs.exclude(collector_config_id=self.collector_config_id)
        return qs.exists()

    def _update_or_create_subscription(self, collector_scenario, params: dict, is_create=False):
        try:
            self.data.subscription_id = collector_scenario.update_or_create_subscription(self.data, params)
            self.data.save()
            if params.get("run_task", True):
                self._run_subscription_task()
            # start nodeman subscription
            NodeApi.switch_subscription(
                {"subscription_id": self.data.subscription_id, "action": "enable", "bk_biz_id": self.data.bk_biz_id}
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.exception(f"create or update collector config failed => [{error}]")
            if not is_create:
                raise CollectorCreateOrUpdateSubscriptionException(
                    CollectorCreateOrUpdateSubscriptionException.MESSAGE.format(err=error)
                )

    def _authorization_collector(self, collector_config: CollectorConfig):
        try:
            # 如果是创建，需要做新建授权
            Permission().grant_creator_action(
                resource=ResourceEnum.COLLECTION.create_simple_instance(
                    collector_config.collector_config_id, attribute={"name": collector_config.collector_config_name}
                ),
                creator=collector_config.created_by,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "collector_config->({}) grant creator action failed, reason: {}".format(
                    collector_config.collector_config_id, e
                )
            )

    @transaction.atomic
    def destroy(self, **kwargs):
        """
        删除采集配置
        :return: task_id
        """
        # 1. 重新命名采集项名称
        collector_config_name = (
            self.data.collector_config_name + "_delete_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        )

        # 2. 停止采集（删除配置文件）
        self.stop()

        if self.data.is_container_environment:
            ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id).delete()

        # 3. 节点管理-删除订阅配置
        self._delete_subscription()

        # 4. 删除索引集
        if self.data.index_set_id:
            index_set_handler = IndexSetHandler(index_set_id=self.data.index_set_id)
            index_set_handler.delete(self.data.collector_config_name)

        # 5. 删除CollectorConfig记录
        self.data.collector_config_name = collector_config_name
        self.data.save()
        self.data.delete()

        # 6. 删除META采集项：直接重命名采集项名称
        collector_scenario = CollectorScenario.get_instance(collector_scenario_id=self.data.collector_scenario_id)
        if self.data.bk_data_id:
            collector_scenario.delete_data_id(self.data.bk_data_id, collector_config_name)

        # 7. 如果存在归档使用了当前采集项, 则删除归档
        qs = ArchiveConfig.objects.filter(
            instance_id=self.data.collector_config_id, instance_type=ArchiveInstanceType.COLLECTOR_CONFIG.value
        )
        if qs.exists():
            qs.delete()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.DESTROY,
            "params": "",
        }
        user_operation_record.delay(operation_record)

        return True

    def run(self, action, scope):
        if self.data.subscription_id:
            return self._run_subscription_task(action=action, scope=scope)
        return True

    @transaction.atomic
    def start(self, **kwargs):
        """
        启动采集配置
        :return: task_id
        """
        self._itsm_start_judge()

        self.data.is_active = True
        self.data.save()

        # 启用采集项
        if self.data.index_set_id:
            index_set_handler = IndexSetHandler(self.data.index_set_id)
            index_set_handler.start()

        if self.data.is_container_environment:
            container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id)
            for container_config in container_configs:
                self.create_container_release(container_config)

        # 启动节点管理订阅功能
        if self.data.subscription_id:
            NodeApi.switch_subscription(
                {"subscription_id": self.data.subscription_id, "action": "enable", "bk_biz_id": self.data.bk_biz_id}
            )

        # 存在RT则启用RT
        if self.data.table_id:
            _, table_id = self.data.table_id.split(".")  # pylint: disable=unused-variable
            etl_storage = EtlStorage.get_instance(self.data.etl_config)
            etl_storage.switch_result_table(collector_config=self.data, is_enable=True)

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.START,
            "params": "",
        }
        user_operation_record.delay(operation_record)

        if self.data.subscription_id:
            return self._run_subscription_task()
        return True

    def _itsm_start_judge(self):
        if self.data.is_custom_scenario:
            return
        if self.data.itsm_has_appling() and FeatureToggleObject.switch(name=FEATURE_COLLECTOR_ITSM):
            raise CollectNotSuccessNotCanStart

    @transaction.atomic
    def stop(self, **kwargs):
        """
        停止采集配置
        :return: task_id
        """
        self.data.is_active = False
        self.data.save()

        # 停止采集项
        if self.data.index_set_id:
            index_set_handler = IndexSetHandler(self.data.index_set_id)
            index_set_handler.stop()

        if self.data.is_container_environment:
            container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id)
            for container_config in container_configs:
                self.delete_container_release(container_config)

        if self.data.subscription_id:
            # 停止节点管理订阅功能
            NodeApi.switch_subscription(
                {"subscription_id": self.data.subscription_id, "action": "disable", "bk_biz_id": self.data.bk_biz_id}
            )

        # 存在RT则停止RT
        if self.data.table_id:
            _, table_id = self.data.table_id.split(".")  # pylint: disable=unused-variable
            etl_storage = EtlStorage.get_instance(self.data.etl_config)
            etl_storage.switch_result_table(collector_config=self.data, is_enable=False)

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.STOP,
            "params": "",
        }
        user_operation_record.delay(operation_record)

        if self.data.subscription_id:
            return self._run_subscription_task("STOP")
        return True

    @classmethod
    def _get_kafka_broker(cls, broker_url):
        """
        判断是否为内网域名
        """
        if "consul" in broker_url and settings.DEFAULT_KAFKA_HOST:
            return settings.DEFAULT_KAFKA_HOST
        return broker_url

    def tail(self):
        if not self.data.bk_data_id:
            raise CollectorConfigDataIdNotExistException()
        data_result = TransferApi.list_kafka_tail(params={"bk_data_id": self.data.bk_data_id, "namespace": "bklog"})
        return_data = []
        for _message in data_result:
            # 数据预览
            etl_message = copy.deepcopy(_message)
            data_items = etl_message.get("items")
            if data_items:
                etl_message.update(
                    {
                        "data": data_items[0].get("data", ""),
                        "log": data_items[0].get("data", ""),
                        "iterationindex": data_items[0].get("iterationindex", ""),
                        "batch": [_item.get("data", "") for _item in data_items],
                    }
                )
            else:
                etl_message.update({"data": "", "iterationindex": "", "bathc": []})

            return_data.append({"etl": etl_message, "origin": _message})

        return return_data

    def retry_instances(self, instance_id_list):
        if self.data.is_container_environment:
            return self.retry_container_collector(instance_id_list)
        return self.retry_target_nodes(instance_id_list)

    def retry_container_collector(self, container_collector_config_id_list=None, **kwargs):
        """
        retry_container_collector
        @param container_collector_config_id_list:
        @return:
        """
        container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.data.collector_config_id)
        if container_collector_config_id_list:
            container_configs = container_configs.filter(id__in=container_collector_config_id_list)

        for container_config in container_configs:
            self.create_container_release(container_config)
        return [config.id for config in container_configs]

    def retry_target_nodes(self, instance_id_list):
        """
        重试部分实例或主机
        @param instance_id_list:
        @return:
        """
        res = self._retry_subscription(instance_id_list=instance_id_list)

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.RETRY,
            "params": {"instance_id_list": instance_id_list},
        }
        user_operation_record.delay(operation_record)

        return res

    def _run_subscription_task(self, action=None, scope: dict[str, Any] = None):
        """
        触发订阅事件
        :param: action 动作 [START, STOP, INSTALL, UNINSTALL]
        :param: nodes 需要重试的实例
        :return: task_id 任务ID
        """
        collector_scenario = CollectorScenario.get_instance(collector_scenario_id=self.data.collector_scenario_id)
        params = {"subscription_id": self.data.subscription_id, "bk_biz_id": self.data.bk_biz_id}
        if action:
            params.update({"actions": {collector_scenario.PLUGIN_NAME: action}})

        # 无scope.nodes时，节点管理默认对全部已配置的scope.nodes进行操作
        # 有scope.nodes时，对指定scope.nodes进行操作
        if scope:
            params["scope"] = scope
            params["scope"]["bk_biz_id"] = self.data.bk_biz_id

        task_id = str(NodeApi.run_subscription_task(params)["task_id"])
        if scope is None:
            self.data.task_id_list = [str(task_id)]
        self.data.save()
        return self.data.task_id_list

    def _retry_subscription(self, instance_id_list):
        params = {
            "subscription_id": self.data.subscription_id,
            "instance_id_list": instance_id_list,
            "bk_biz_id": self.data.bk_biz_id,
        }

        task_id = str(NodeApi.retry_subscription(params)["task_id"])
        self.data.task_id_list.append(task_id)
        self.data.save()
        return self.data.task_id_list

    def _delete_subscription(self):
        """
        删除订阅事件
        :return: [dict]
        {
            "message": "",
            "code": "OK",
            "data": null,
            "result": true
        }
        """
        if not self.data.subscription_id:
            return
        subscription_params = {"subscription_id": self.data.subscription_id, "bk_biz_id": self.data.bk_biz_id}
        return NodeApi.delete_subscription(subscription_params)

    def diff_target_nodes(self, target_nodes: list) -> list:
        """
        比较订阅节点的变化
        :param target_nodes 目标节点
        :return
        [
            {
                'type': 'add',
                'bk_inst_id': 2,
                'bk_obj_id': 'biz'
            },
            {
                'type': 'add',
                'bk_inst_id': 3,
                'bk_obj_id': 'module'
            },
            {
                'type': 'delete',
                'bk_inst_id': 4,
                'bk_obj_id': 'set'
            },
            {
                'type': 'modify',
                'bk_inst_id': 5,
                'bk_obj_id': 'module'
            }
        ]
        """

        def genera_nodes_tuples(nodes):
            return [
                (node["bk_inst_id"], node["bk_obj_id"]) for node in nodes if "bk_inst_id" in node or "bk_obj_id" in node
            ]

        current_nodes_tuples = genera_nodes_tuples(self.data.target_nodes)
        target_nodes_tuples = genera_nodes_tuples(target_nodes)
        add_nodes = [
            {"type": "add", "bk_inst_id": node[0], "bk_obj_id": node[1]}
            for node in set(target_nodes_tuples) - set(current_nodes_tuples)
        ]
        delete_nodes = [
            {"type": "delete", "bk_inst_id": node[0], "bk_obj_id": node[1]}
            for node in set(current_nodes_tuples) - set(target_nodes_tuples)
        ]
        return add_nodes + delete_nodes

    def get_task_status(self, id_list):
        if self.data.is_container_environment:
            return self.get_container_collect_status(container_collector_config_id_list=id_list)
        return self.get_subscription_task_status(task_id_list=id_list)

    def get_container_collect_status(self, container_collector_config_id_list):
        """
        查询容器采集任务状态
        """
        container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.data.collector_config_id)
        if container_collector_config_id_list:
            container_configs = container_configs.filter(id__in=container_collector_config_id_list)

        contents = []
        for container_config in container_configs:
            contents.append(
                {
                    "message": container_config.status_detail,
                    "status": container_config.status,
                    "container_collector_config_id": container_config.id,
                    "name": self.generate_bklog_config_name(container_config.id),
                }
            )
        return {
            "contents": [
                {
                    "collector_config_id": self.data.collector_config_id,
                    "collector_config_name": self.data.collector_config_name,
                    "child": contents,
                }
            ]
        }

    def get_subscription_task_status(self, task_id_list):
        """
        查询采集任务状态
        :param  [list] task_id_list:
        :return: [dict]
        {
            "contents": [
                {
                "is_label": true,
                "label_name": "modify",
                "bk_obj_name": "模块",
                "node_path": "蓝鲸_test1_配置平台_adminserver",
                "bk_obj_id": "module",
                "bk_inst_id": 33,
                "bk_inst_name": "adminserver",
                "child": [
                    {
                        "bk_host_id": 1,
                        "status": "FAILED",
                        "ip": "127.0.0.1",
                        "bk_cloud_id": 0,
                        "log": "[unifytlogc] 下发插件配置-重载插件进程",
                        "instance_id": "host|instance|host|127.0.0.1-0-0",
                        "instance_name": "127.0.0.1",
                        "task_id": 24516,
                        "bk_supplier_id": "0",
                        "create_time": "2019-09-17 19:23:02",
                        "steps": {1 item}
                        }
                    ]
                }
            ]
        }
        """
        if self.data.is_custom_scenario:
            return {"task_ready": True, "contents": []}

        if not self.data.subscription_id:
            self._update_or_create_subscription(
                collector_scenario=CollectorScenario.get_instance(
                    collector_scenario_id=self.data.collector_scenario_id
                ),
                params=self.data.params,
            )
        # 查询采集任务状态
        param = {
            "subscription_id": self.data.subscription_id,
            "bk_biz_id": self.data.bk_biz_id,
        }
        if self.data.task_id_list:
            param["task_id_list"] = self.data.task_id_list

        task_ready = self._check_task_ready(param=param)

        # 如果任务未启动，则直接返回结果
        if not task_ready:
            return {"task_ready": task_ready, "contents": []}

        status_result = NodeApi.get_subscription_task_status.bulk_request(
            params={
                "subscription_id": self.data.subscription_id,
                "need_detail": False,
                "need_aggregate_all_tasks": True,
                "need_out_of_scope_snapshots": False,
                "bk_biz_id": self.data.bk_biz_id,
            },
            get_data=lambda x: x["list"],
            get_count=lambda x: x["total"],
        )
        instance_status = self.format_task_instance_status(status_result)

        # 如果采集目标是HOST-INSTANCE
        if self.data.target_node_type == TargetNodeTypeEnum.INSTANCE.value:
            content_data = [
                {
                    "is_label": False,
                    "label_name": "",
                    "bk_obj_name": _("主机"),
                    "node_path": _("主机"),
                    "bk_obj_id": "host",
                    "bk_inst_id": "",
                    "bk_inst_name": "",
                    "child": instance_status,
                }
            ]
            return {"task_ready": task_ready, "contents": content_data}

        # 如果采集目标是HOST-TOPO
        # 获取target_nodes获取采集目标及差异节点target_subscription_diff合集
        node_collect = self._get_collect_node()
        node_mapping, template_mapping = self._get_mapping(node_collect=node_collect)
        content_data = list()
        target_mapping = self.get_target_mapping()
        total_host_result = self._get_host_result(node_collect)
        for node_obj in node_collect:
            map_key = "{}|{}".format(str(node_obj["bk_obj_id"]), str(node_obj["bk_inst_id"]))
            host_result = total_host_result.get(map_key, [])
            label_name = target_mapping.get(map_key, "")
            node_path, bk_obj_name, bk_inst_name = self._get_node_obj(
                node_obj=node_obj, template_mapping=template_mapping, node_mapping=node_mapping, map_key=map_key
            )

            content_obj = {
                "is_label": False if not label_name else True,
                "label_name": label_name,
                "bk_obj_name": bk_obj_name,
                "node_path": node_path,
                "bk_obj_id": node_obj["bk_obj_id"],
                "bk_inst_id": node_obj["bk_inst_id"],
                "bk_inst_name": bk_inst_name,
                "child": [],
            }

            for instance_obj in instance_status:
                # delete 标签如果订阅任务状态action不为UNINSTALL
                if label_name == "delete" and instance_obj["steps"].get(LogPluginInfo.NAME) != "UNINSTALL":
                    continue
                # 因为instance_obj兼容新版IP选择器的字段名, 所以这里的bk_cloud_id->cloud_id, bk_host_id->host_id
                if (instance_obj["ip"], instance_obj["cloud_id"]) in host_result or instance_obj[
                    "host_id"
                ] in host_result:
                    content_obj["child"].append(instance_obj)
            content_data.append(content_obj)
        return {"task_ready": task_ready, "contents": content_data}

    def _check_task_ready(self, param: dict):
        """
        查询任务是否下发: 兼容节点管理未发布的情况
        @param param {Dict} NodeApi.check_subscription_task_ready 请求
        """
        try:
            task_ready = NodeApi.check_subscription_task_ready(param)
        # 如果节点管理路由不存在或服务异常等request异常情况
        except BaseException as e:  # pylint: disable=broad-except
            task_ready = self._check_task_ready_exception(e)
        return task_ready

    def _get_collect_node(self):
        """
        获取target_nodes和target_subscription_diff集合之后组成的node_collect
        """
        node_collect = copy.deepcopy(self.data.target_nodes)
        for target_obj in self.data.target_subscription_diff:
            node_dic = {"bk_inst_id": target_obj["bk_inst_id"], "bk_obj_id": target_obj["bk_obj_id"]}
            if node_dic not in node_collect:
                node_collect.append(node_dic)
        return node_collect

    def _get_host_result(self, node_collect):
        """
        根据业务、节点查询主机
        node_collect {List} _get_collect_node处理后组成的node_collect
        """
        conditions = [
            {"bk_obj_id": node_obj["bk_obj_id"], "bk_inst_id": node_obj["bk_inst_id"]} for node_obj in node_collect
        ]
        host_result = BizHandler(self.data.bk_biz_id).search_host(conditions)
        host_result_dict = defaultdict(list)
        for host in host_result:
            for inst_id in host["parent_inst_id"]:
                key = "{}|{}".format(str(host["bk_obj_id"]), str(inst_id))
                host_result_dict[key].append((host["bk_host_innerip"], host["bk_cloud_id"]))
                host_result_dict[key].append(host["bk_host_id"])
        return host_result_dict

    def _get_mapping(self, node_collect):
        """
        查询业务TOPO，按采集目标节点进行分类
        node_collect {List} _get_collect_node处理后组成的node_collect
        """
        biz_topo = self._get_biz_topo()
        node_mapping = self.get_node_mapping(biz_topo)
        template_mapping = self._get_template_mapping(node_collect=node_collect)

        return node_mapping, template_mapping

    def _get_biz_topo(self):
        """
        查询业务TOPO，按采集目标节点进行分类
        """
        biz_topo = CCApi.search_biz_inst_topo({"bk_biz_id": self.data.bk_biz_id, "level": SEARCH_BIZ_INST_TOPO_LEVEL})
        try:
            internal_topo = self.get_biz_internal_module()
            if internal_topo:
                biz_topo[BIZ_TOPO_INDEX]["child"].insert(INTERNAL_TOPO_INDEX, internal_topo)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"call CCApi.search_biz_inst_topo error: {e}")
            pass
        return biz_topo

    def _get_template_mapping(self, node_collect):
        """
        获取模板dict
        @param node_collect {List} _get_collect_node处理后组成的node_collect
        """
        service_template_mapping = {}
        set_template_mapping = {}
        bk_boj_id_set = {node_obj["bk_obj_id"] for node_obj in node_collect}

        if TargetNodeTypeEnum.SERVICE_TEMPLATE.value in bk_boj_id_set:
            service_templates = CCApi.list_service_template.bulk_request({"bk_biz_id": self.data.bk_biz_id})
            service_template_mapping = {
                "{}|{}".format(TargetNodeTypeEnum.SERVICE_TEMPLATE.value, str(template.get("id", ""))): {
                    "name": template.get("name")
                }
                for template in service_templates
            }

        if TargetNodeTypeEnum.SET_TEMPLATE.value in bk_boj_id_set:
            set_templates = CCApi.list_set_template.bulk_request({"bk_biz_id": self.data.bk_biz_id})
            set_template_mapping = {
                "{}|{}".format(TargetNodeTypeEnum.SET_TEMPLATE.value, str(template.get("id", ""))): {
                    "name": template.get("name")
                }
                for template in set_templates
            }

        return {**service_template_mapping, **set_template_mapping}

    @classmethod
    def _get_node_obj(cls, node_obj, template_mapping, node_mapping, map_key):
        """
        获取node_path, bk_obj_name, bk_inst_name
        @param node_obj {dict} _get_collect_node处理后组成的node_collect对应元素
        @param template_mapping {dict} 模板集合
        @param node_mapping {dict} 拓扑节点集合
        @param map_key {str} 集合对应key
        """

        if node_obj["bk_obj_id"] in [
            TargetNodeTypeEnum.SET_TEMPLATE.value,
            TargetNodeTypeEnum.SERVICE_TEMPLATE.value,
        ]:
            node_path = template_mapping.get(map_key, {}).get("name", "")
            bk_obj_name = TargetNodeTypeEnum.get_choice_label(node_obj["bk_obj_id"])
            bk_inst_name = template_mapping.get(map_key, {}).get("name", "")
            return node_path, bk_obj_name, bk_inst_name

        node_path = "_".join(
            [node_mapping.get(node).get("bk_inst_name") for node in node_mapping.get(map_key, {}).get("node_link", [])]
        )
        bk_obj_name = node_mapping.get(map_key, {}).get("bk_obj_name", "")
        bk_inst_name = node_mapping.get(map_key, {}).get("bk_inst_name", "")

        return node_path, bk_obj_name, bk_inst_name

    @classmethod
    def _check_task_ready_exception(cls, error: BaseException):
        """
        处理task_ready_exception 返回error
        @param error {BaseException} 返回错误
        """
        task_ready = True
        if isinstance(error, ApiRequestError):
            return task_ready
        if isinstance(error, ApiResultError) and str(error.code) == CHECK_TASK_READY_NOTE_FOUND_EXCEPTION_CODE:
            return task_ready
        logger.error(f"Call NodeApi check_task_ready error: {error}")
        raise error

    def format_task_instance_status(self, instance_data):
        """
        格式化任务状态数据
        :param  [list] instance_data: 任务状态data数据
        :return: [list]
        """
        instance_list = list()
        host_list = list()
        latest_id = self.data.task_id_list[-1]
        if self.data.target_node_type == TargetNodeTypeEnum.INSTANCE.value:
            for node in self.data.target_nodes:
                if "bk_host_id" in node:
                    host_list.append(node["bk_host_id"])
                else:
                    host_list.append((node["ip"], node["bk_cloud_id"]))

        for instance_obj in instance_data:
            bk_cloud_id = instance_obj["instance_info"]["host"]["bk_cloud_id"]
            if isinstance(bk_cloud_id, list):
                bk_cloud_id = bk_cloud_id[0]["bk_inst_id"]
            bk_host_innerip = instance_obj["instance_info"]["host"]["bk_host_innerip"]
            bk_host_id = instance_obj["instance_info"]["host"]["bk_host_id"]

            # 静态节点：排除订阅任务历史IP（不是最新订阅且不在当前节点范围的ip）
            if (
                self.data.target_node_type == TargetNodeTypeEnum.INSTANCE.value
                and str(instance_obj["task_id"]) != latest_id
                and ((bk_host_innerip, bk_cloud_id) not in host_list and bk_host_id not in host_list)
            ):
                continue
            instance_list.append(
                {
                    "host_id": bk_host_id,
                    "status": instance_obj["status"],
                    "ip": bk_host_innerip,
                    "ipv6": instance_obj["instance_info"]["host"].get("bk_host_innerip_v6", ""),
                    "host_name": instance_obj["instance_info"]["host"]["bk_host_name"],
                    "cloud_id": bk_cloud_id,
                    "log": self.get_instance_log(instance_obj),
                    "instance_id": instance_obj["instance_id"],
                    "instance_name": bk_host_innerip,
                    "task_id": instance_obj.get("task_id", ""),
                    "bk_supplier_id": instance_obj["instance_info"]["host"].get("bk_supplier_account"),
                    "create_time": instance_obj["create_time"],
                    "steps": {i["id"]: i["action"] for i in instance_obj.get("steps", []) if i["action"]},
                }
            )
        return instance_list

    @staticmethod
    def get_instance_log(instance_obj):
        """
        获取采集实例日志
        :param  [dict] instance_obj: 实例状态日志
        :return: [string]
        """
        for step_obj in instance_obj.get("steps", []):
            if step_obj == CollectStatus.SUCCESS:
                continue
            for sub_step_obj in step_obj["target_hosts"][0]["sub_steps"]:
                if sub_step_obj["status"] != CollectStatus.SUCCESS:
                    return "{}-{}".format(step_obj["node_name"], sub_step_obj["node_name"])
        return ""

    def get_node_mapping(self, topo_tree):
        """
        节点映射关系
        :param  [list] topo_tree: 拓扑树
        :return: [dict]
        """
        node_mapping = {}

        def mapping(node, node_link, node_mapping):
            node.update(node_link=node_link)
            node_mapping[node_link[-1]] = node

        BizHandler().foreach_topo_tree(topo_tree, mapping, node_mapping=node_mapping)
        return node_mapping

    def get_target_mapping(self) -> dict:
        """
        节点和标签映射关系
        :return: [dict] {"module|33": "modify", "set|6": "add", "set|7": "delete"}
        """
        target_mapping = dict()
        for target in self.data.target_subscription_diff:
            key = "{}|{}".format(target["bk_obj_id"], target["bk_inst_id"])
            target_mapping[key] = target["type"]
        return target_mapping

    def get_subscription_task_detail(self, instance_id, task_id=None):
        """
        采集任务实例日志详情
        :param [string] instance_id: 实例ID
        :param [string] task_id: 任务ID
        :return: [dict]
        """
        # 详情接口查询，原始日志
        param = {
            "subscription_id": self.data.subscription_id,
            "instance_id": instance_id,
            "bk_biz_id": self.data.bk_biz_id,
        }
        if task_id:
            param["task_id"] = task_id
        detail_result = NodeApi.get_subscription_task_detail(param)

        # 日志详情，用于前端展示
        log = list()
        for step in detail_result.get("steps", []):
            log.append("{}{}{}\n".format("=" * 20, step["node_name"], "=" * 20))
            for sub_step in step["target_hosts"][0].get("sub_steps", []):
                log.extend(["{}{}{}".format("-" * 20, sub_step["node_name"], "-" * 20), sub_step["log"]])
                # 如果ex_data里面有值，则在日志里加上它
                if sub_step["ex_data"]:
                    log.append(sub_step["ex_data"])
                if sub_step["status"] != CollectStatus.SUCCESS:
                    return {"log_detail": "\n".join(log), "log_result": detail_result}
        return {"log_detail": "\n".join(log), "log_result": detail_result}

    def get_subscription_status_by_list(self, collector_id_list: list) -> list:
        """
        批量获取采集项订阅状态
        :param  [list] collector_id_list: 采集项ID列表
        :return: [dict]
        """
        return_data = list()
        subscription_id_list = list()
        subscription_collector_map = dict()

        collector_list = CollectorConfig.objects.filter(collector_config_id__in=collector_id_list)

        # 获取主采集项到容器子采集项的映射关系
        container_collector_mapping = defaultdict(list)
        for config in ContainerCollectorConfig.objects.filter(collector_config_id__in=collector_id_list):
            container_collector_mapping[config.collector_config_id].append(config)

        for collector_obj in collector_list:
            if collector_obj.is_container_environment:
                container_collector_configs = container_collector_mapping[collector_obj.collector_config_id]

                failed_count = 0
                success_count = 0
                pending_count = 0

                # 默认是成功
                status = CollectStatus.SUCCESS
                status_name = RunStatus.SUCCESS

                for config in container_collector_configs:
                    if config.status == ContainerCollectStatus.FAILED.value:
                        failed_count += 1
                    elif config.status in [ContainerCollectStatus.PENDING.value, ContainerCollectStatus.RUNNING.value]:
                        pending_count += 1
                        status = CollectStatus.RUNNING
                        status_name = RunStatus.RUNNING
                    else:
                        success_count += 1

                if failed_count:
                    status = CollectStatus.FAILED
                    if success_count:
                        # 失败和成功都有，那就是部分失败
                        status_name = RunStatus.PARTFAILED
                    else:
                        status_name = RunStatus.FAILED

                return_data.append(
                    {
                        "collector_id": collector_obj.collector_config_id,
                        "subscription_id": None,
                        "status": status,
                        "status_name": status_name,
                        "total": len(container_collector_configs),
                        "success": success_count,
                        "failed": failed_count,
                        "pending": pending_count,
                    }
                )
                continue

            # 若订阅ID未写入
            if not collector_obj.subscription_id:
                return_data.append(
                    {
                        "collector_id": collector_obj.collector_config_id,
                        "subscription_id": None,
                        "status": CollectStatus.PREPARE if collector_obj.target_nodes else CollectStatus.SUCCESS,
                        "status_name": RunStatus.PREPARE if collector_obj.target_nodes else RunStatus.SUCCESS,
                        "total": 0,
                        "success": 0,
                        "failed": 0,
                        "pending": 0,
                    }
                )
                continue

            # 订阅ID和采集配置ID的映射关系 & 需要查询订阅ID列表
            subscription_collector_map[collector_obj.subscription_id] = collector_obj.collector_config_id
            subscription_id_list.append(collector_obj.subscription_id)

        status_result = NodeApi.subscription_statistic(
            params={
                "subscription_id_list": subscription_id_list,
                "plugin_name": LogPluginInfo.NAME,
            }
        )

        # 如果没有订阅ID，则直接返回
        if not subscription_id_list:
            return self._clean_terminated(return_data)

        # 接口查询到的数据进行处理
        subscription_status_data, subscription_id_list = self.format_subscription_status(
            status_result, subscription_id_list, subscription_collector_map
        )
        return_data += subscription_status_data

        # 节点管理接口未查到相应订阅ID数据
        for subscription_id in subscription_id_list:
            collector_key = subscription_collector_map[subscription_id]
            return_data.append(
                {
                    "collector_id": collector_key,
                    "subscription_id": subscription_id,
                    "status": CollectStatus.FAILED,
                    "status_name": RunStatus.FAILED,
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "pending": 0,
                }
            )

        # 若采集项已停用，则采集状态修改为“已停用”
        return self._clean_terminated(return_data)

    def _clean_terminated(self, data: list):
        for _data in data:
            # RUNNING状态
            if _data["status"] == CollectStatus.RUNNING:
                continue

            _collector_config = CollectorConfig.objects.get(collector_config_id=_data["collector_id"])
            if not _collector_config.is_active:
                _data["status"] = CollectStatus.TERMINATED
                _data["status_name"] = RunStatus.TERMINATED
        return data

    def format_subscription_status(self, status_result, subscription_id_list, subscription_collector_map):
        return_data = list()

        for status_obj in status_result:
            total_count = int(status_obj["instances"])
            status_group = {
                status["status"]: int(status["count"]) for status in status_obj["status"] if status["count"]
            }

            # 订阅状态
            group_status_keys = status_group.keys()
            if not status_group:
                status = CollectStatus.UNKNOWN
                status_name = RunStatus.UNKNOWN
            elif CollectStatus.PENDING in group_status_keys or CollectStatus.RUNNING in group_status_keys:
                status = CollectStatus.RUNNING
                status_name = RunStatus.RUNNING
            elif CollectStatus.FAILED in group_status_keys and CollectStatus.SUCCESS in group_status_keys:
                status = CollectStatus.FAILED
                status_name = RunStatus.PARTFAILED
            elif CollectStatus.FAILED in group_status_keys and CollectStatus.SUCCESS not in group_status_keys:
                status = CollectStatus.FAILED
                status_name = RunStatus.FAILED
            elif CollectStatus.TERMINATED in group_status_keys and CollectStatus.SUCCESS not in group_status_keys:
                status = CollectStatus.TERMINATED
                status_name = RunStatus.TERMINATED
            else:
                status = CollectStatus.SUCCESS
                status_name = RunStatus.SUCCESS

            # 各订阅状态实例数量
            pending_count = status_group.get(CollectStatus.PENDING, 0) + status_group.get(CollectStatus.RUNNING, 0)
            failed_count = status_group.get(CollectStatus.FAILED, 0)
            success_count = status_group.get(CollectStatus.SUCCESS, 0)

            subscription_id_list.remove(status_obj["subscription_id"])
            return_data.append(
                {
                    "collector_id": subscription_collector_map[status_obj["subscription_id"]],
                    "subscription_id": status_obj["subscription_id"],
                    "status": status,
                    "status_name": status_name,
                    "total": total_count,
                    "success": success_count,
                    "failed": failed_count,
                    "pending": pending_count,
                }
            )
        return return_data, subscription_id_list

    def get_subscription_status(self):
        """
        查看订阅的插件运行状态
        :return:
        """
        if self.data.is_container_environment:
            # 容器采集特殊处理
            container_configs = ContainerCollectorConfig.objects.filter(
                collector_config_id=self.data.collector_config_id
            )

            contents = []
            for container_config in container_configs:
                contents.append(
                    {"status": container_config.status, "container_collector_config_id": container_config.id}
                )
            return {
                "contents": [
                    {
                        "collector_config_id": self.data.collector_config_id,
                        "collector_config_name": self.data.collector_config_name,
                        "child": contents,
                    }
                ]
            }

        if not self.data.subscription_id and not self.data.target_nodes:
            return {
                "contents": [
                    {
                        "is_label": False,
                        "label_name": "",
                        "bk_obj_name": _("主机"),
                        "node_path": _("主机"),
                        "bk_obj_id": "host",
                        "bk_inst_id": "",
                        "bk_inst_name": "",
                        "child": [],
                    }
                ]
            }
        instance_data = NodeApi.get_subscription_task_status.bulk_request(
            params={
                "subscription_id": self.data.subscription_id,
                "need_detail": False,
                "need_aggregate_all_tasks": True,
                "need_out_of_scope_snapshots": False,
                "bk_biz_id": self.data.bk_biz_id,
            },
            get_data=lambda x: x["list"],
            get_count=lambda x: x["total"],
        )

        bk_host_ids = []
        for item in instance_data:
            bk_host_ids.append(item["instance_info"]["host"]["bk_host_id"])

        plugin_data = NodeApi.plugin_search.batch_request(
            params={
                "conditions": [],
                "page": 1,
                "pagesize": settings.BULK_REQUEST_LIMIT,
                "bk_biz_id": self.data.bk_biz_id,
            },
            chunk_values=bk_host_ids,
            chunk_key="bk_host_id",
        )

        instance_status = self.format_subscription_instance_status(instance_data, plugin_data)

        # 如果采集目标是HOST-INSTANCE
        if self.data.target_node_type == TargetNodeTypeEnum.INSTANCE.value:
            content_data = [
                {
                    "is_label": False,
                    "label_name": "",
                    "bk_obj_name": _("主机"),
                    "node_path": _("主机"),
                    "bk_obj_id": "host",
                    "bk_inst_id": "",
                    "bk_inst_name": "",
                    "child": instance_status,
                }
            ]
            return {"contents": content_data}

        # 如果采集目标是HOST-TOPO
        # 从数据库target_nodes获取采集目标，查询业务TOPO，按采集目标节点进行分类
        target_nodes = self.data.target_nodes
        biz_topo = self._get_biz_topo()

        node_mapping = self.get_node_mapping(biz_topo)
        template_mapping = self._get_template_mapping(target_nodes)
        total_host_result = self._get_host_result(node_collect=target_nodes)

        content_data = list()
        for node_obj in target_nodes:
            map_key = "{}|{}".format(str(node_obj["bk_obj_id"]), str(node_obj["bk_inst_id"]))
            host_result = total_host_result.get(map_key, [])
            node_path, bk_obj_name, bk_inst_name = self._get_node_obj(
                node_obj=node_obj, template_mapping=template_mapping, node_mapping=node_mapping, map_key=map_key
            )
            content_obj = {
                "is_label": False,
                "label_name": "",
                "bk_obj_name": bk_obj_name,
                "node_path": node_path,
                "bk_obj_id": node_obj["bk_obj_id"],
                "bk_inst_id": node_obj["bk_inst_id"],
                "bk_inst_name": bk_inst_name,
                "child": [],
            }

            for instance_obj in instance_status:
                # 因为instance_obj兼容新版IP选择器的字段名, 所以这里的bk_cloud_id->cloud_id, bk_host_id->host_id
                if (instance_obj["ip"], instance_obj["cloud_id"]) in host_result or instance_obj[
                    "host_id"
                ] in host_result:
                    content_obj["child"].append(instance_obj)
            content_data.append(content_obj)
        return {"contents": content_data}

    @staticmethod
    def format_subscription_instance_status(instance_data, plugin_data):
        """
        对订阅状态数据按照实例运行状态进行归类
        :param [dict] instance_data:
        :param [dict] plugin_data:
        :return: [dict]
        """
        plugin_status_mapping = {}
        for plugin_obj in plugin_data:
            for item in plugin_obj["plugin_status"]:
                if item["name"] == "bkunifylogbeat":
                    plugin_status_mapping[plugin_obj["bk_host_id"]] = item

        instance_list = list()
        for instance_obj in instance_data:
            # 日志采集暂时只支持本地采集
            bk_host_id = instance_obj["instance_info"]["host"]["bk_host_id"]
            plugin_statuses = plugin_status_mapping.get(bk_host_id, {})
            if instance_obj["status"] in [CollectStatus.PENDING, CollectStatus.RUNNING]:
                status = CollectStatus.RUNNING
                status_name = RunStatus.RUNNING
            elif instance_obj["status"] == CollectStatus.SUCCESS:
                status = CollectStatus.SUCCESS
                status_name = RunStatus.SUCCESS
            else:
                status = CollectStatus.FAILED
                status_name = RunStatus.FAILED

            bk_cloud_id = instance_obj["instance_info"]["host"]["bk_cloud_id"]
            if isinstance(bk_cloud_id, list):
                bk_cloud_id = bk_cloud_id[0]["bk_inst_id"]

            status_obj = {
                "status": status,
                "status_name": status_name,
                "host_id": bk_host_id,
                "ip": instance_obj["instance_info"]["host"]["bk_host_innerip"],
                "ipv6": instance_obj["instance_info"]["host"].get("bk_host_innerip_v6", ""),
                "cloud_id": bk_cloud_id,
                "host_name": instance_obj["instance_info"]["host"]["bk_host_name"],
                "instance_id": instance_obj["instance_id"],
                "instance_name": instance_obj["instance_info"]["host"]["bk_host_innerip"],
                "plugin_name": plugin_statuses.get("name"),
                "plugin_version": plugin_statuses.get("version"),
                "bk_supplier_id": instance_obj["instance_info"]["host"].get("bk_supplier_account"),
                "create_time": instance_obj["create_time"],
            }
            instance_list.append(status_obj)

        return instance_list

    @staticmethod
    def regex_debug(data):
        """
        行首正则调试，返回匹配行数
        """
        lines = data["log_sample"].split("\n")
        match_lines = 0
        for line in lines:
            try:
                if re.search(data["multiline_pattern"], line):
                    match_lines += 1
            except re.error as e:
                raise RegexInvalidException(RegexInvalidException.MESSAGE.format(error=e))
        if not match_lines:
            raise RegexMatchException
        data.update({"match_lines": match_lines})
        return data

    def get_biz_internal_module(self):
        internal_module = CCApi.get_biz_internal_module(
            {"bk_biz_id": self.data.bk_biz_id, "bk_supplier_account": BK_SUPPLIER_ACCOUNT}
        )
        internal_topo = {
            "host_count": 0,
            "default": 0,
            "bk_obj_name": _("集群"),
            "bk_obj_id": "set",
            "child": [
                {
                    "host_count": 0,
                    "default": _module.get("default", 0),
                    "bk_obj_name": _("模块"),
                    "bk_obj_id": "module",
                    "child": [],
                    "bk_inst_id": _module["bk_module_id"],
                    "bk_inst_name": _module["bk_module_name"],
                }
                for _module in internal_module.get("module", [])
            ],
            "bk_inst_id": internal_module["bk_set_id"],
            "bk_inst_name": internal_module["bk_set_name"],
        }
        return internal_topo

    def indices_info(self):
        result_table_id = self.data.table_id
        if not result_table_id:
            raise CollectNotSuccess
        result = EsRoute(scenario_id=Scenario.LOG, indices=result_table_id).cat_indices()
        return StorageHandler.sort_indices(result)

    def list_collectors_by_host(self, params):
        bk_biz_id = params.get("bk_biz_id")
        node_result = []
        try:
            node_result = NodeApi.query_host_subscriptions({**params, "source_type": "subscription"})
        except ApiRequestError as error:
            if NOT_FOUND_CODE in error.message:
                node_result = []

        subscription_ids = [ip_subscription["source_id"] for ip_subscription in node_result]
        collectors = CollectorConfig.objects.filter(
            subscription_id__in=subscription_ids,
            bk_biz_id=bk_biz_id,
            is_active=True,
            table_id__isnull=False,
            index_set_id__isnull=False,
        )

        collectors = [model_to_dict(c) for c in collectors]
        collectors = self.add_cluster_info(collectors)

        index_sets = {
            index_set.index_set_id: index_set
            for index_set in LogIndexSet.objects.filter(
                index_set_id__in=[collector["index_set_id"] for collector in collectors]
            )
        }

        collect_status = {
            status["collector_id"]: status
            for status in self.get_subscription_status_by_list(
                [collector["collector_config_id"] for collector in collectors]
            )
        }

        return [
            {
                "collector_config_id": collector["collector_config_id"],
                "collector_config_name": collector["collector_config_name"],
                "collector_scenario_id": collector["collector_scenario_id"],
                "index_set_id": collector["index_set_id"],
                "index_set_name": index_sets[collector["index_set_id"]].index_set_name,
                "index_set_scenario_id": index_sets[collector["index_set_id"]].scenario_id,
                "retention": collector["retention"],
                "status": collect_status.get(collector["collector_config_id"], {}).get("status", CollectStatus.UNKNOWN),
                "status_name": collect_status.get(collector["collector_config_id"], {}).get(
                    "status_name", RunStatus.UNKNOWN
                ),
                "description": collector["description"],
            }
            for collector in collectors
            if collector["index_set_id"] in index_sets
        ]

    def cat_illegal_ips(self, params: dict):
        """
        当采集项对应节点为静态主机时判定是否有非法越权IP
        @param params {dict} 创建或者编辑采集项时的请求
        """
        # 这里是为了避免target_node_type, target_nodes参数为空的情况
        target_node_type = params.get("target_node_type")
        target_nodes = params.get("target_nodes", [])
        bk_biz_id = params["bk_biz_id"] if not self.data else self.data.bk_biz_id
        if target_node_type and target_node_type == TargetNodeTypeEnum.INSTANCE.value:
            illegal_ips, illegal_bk_host_ids = self._filter_illegal_ip_and_host_id(
                bk_biz_id=bk_biz_id,
                ips=[target_node["ip"] for target_node in target_nodes if "ip" in target_node],
                bk_host_ids=[target_node["bk_host_id"] for target_node in target_nodes if "bk_host_id" in target_node],
            )
            if illegal_ips or illegal_bk_host_ids:
                illegal_items = [str(item) for item in (illegal_ips + illegal_bk_host_ids)]
                logger.error(f"cat illegal ip or bk_host_id: {illegal_items}")
                raise CollectorIllegalIPException(
                    CollectorIllegalIPException.MESSAGE.format(bk_biz_id=bk_biz_id, illegal_ips=illegal_items)
                )

    @classmethod
    def _filter_illegal_ip_and_host_id(cls, bk_biz_id: int, ips: list = None, bk_host_ids: list = None):
        """
        过滤出非法ip列表
        @param bk_biz_id [Int] 业务id
        @param ips [List] ip列表
        """
        ips = ips or []
        bk_host_ids = bk_host_ids or []
        legal_host_list = CCApi.list_biz_hosts.bulk_request(
            {
                "bk_biz_id": bk_biz_id,
                "host_property_filter": {
                    "condition": "OR",
                    "rules": [
                        {"field": "bk_host_innerip", "operator": "in", "value": ips},
                        {"field": "bk_host_id", "operator": "in", "value": bk_host_ids},
                    ],
                },
                "fields": CMDB_HOST_SEARCH_FIELDS,
            }
        )

        legal_ip_set = {legal_host["bk_host_innerip"] for legal_host in legal_host_list}
        legal_host_id_set = {legal_host["bk_host_id"] for legal_host in legal_host_list}

        illegal_ips = [ip for ip in ips if ip not in legal_ip_set]
        illegal_bk_host_ids = [host_id for host_id in bk_host_ids if host_id not in legal_host_id_set]
        return illegal_ips, illegal_bk_host_ids

    def get_clean_stash(self):
        clean_stash = CleanStash.objects.filter(collector_config_id=self.collector_config_id).first()
        if not clean_stash:
            return None
        config = model_to_dict(clean_stash)
        # 给未配置自定义分词符和大小写敏感的清洗配置添加默认值
        etl_params = config.get("etl_params", {})
        etl_params.setdefault("original_text_is_case_sensitive", False)
        etl_params.setdefault("original_text_tokenize_on_chars", "")
        config["etl_params"] = etl_params

        etl_fields = config.get("etl_fields", [])
        for etl_field in etl_fields:
            etl_field.setdefault("is_case_sensitive", False)
            etl_field.setdefault("tokenize_on_chars", "")
        config["etl_fields"] = etl_fields
        return config

    def create_clean_stash(self, params: dict):
        model_fields = {
            "clean_type": params["clean_type"],
            "etl_params": params["etl_params"],
            "etl_fields": params["etl_fields"],
            "collector_config_id": int(self.collector_config_id),
            "bk_biz_id": params["bk_biz_id"],
        }
        CleanStash.objects.filter(collector_config_id=self.collector_config_id).delete()
        logger.info(f"delete clean stash {self.collector_config_id}")
        return model_to_dict(CleanStash.objects.create(**model_fields))

    def list_collector(self, bk_biz_id):
        return [
            {
                "collector_config_id": collector.collector_config_id,
                "collector_config_name": collector.collector_config_name,
            }
            for collector in CollectorConfig.objects.filter(bk_biz_id=bk_biz_id)
        ]

    @classmethod
    def create_custom_log_group(cls, collector: CollectorConfig):
        resp = TransferApi.create_log_group(
            {
                "bk_data_id": collector.bk_data_id,
                "bk_biz_id": collector.get_bk_biz_id(),
                "log_group_name": collector.collector_config_name_en,
                "label": collector.category_id,
                "operator": collector.created_by,
            }
        )
        collector.log_group_id = resp["log_group_id"]
        collector.save(update_fields=["log_group_id"])

        return resp

    def custom_create(
        self,
        bk_biz_id=None,
        collector_config_name=None,
        collector_config_name_en=None,
        data_link_id=None,
        custom_type=None,
        category_id=None,
        description=None,
        etl_config=None,
        etl_params=None,
        fields=None,
        storage_cluster_id=None,
        retention=7,
        allocation_min_days=0,
        storage_replies=1,
        es_shards=settings.ES_SHARDS,
        bk_app_code=settings.APP_CODE,
        bkdata_biz_id=None,
        is_display=True,
        sort_fields=None,
        target_fields=None,
    ):
        collector_config_params = {
            "bk_biz_id": bk_biz_id,
            "collector_config_name": collector_config_name,
            "collector_config_name_en": collector_config_name_en,
            "collector_scenario_id": CollectorScenarioEnum.CUSTOM.value,
            "custom_type": custom_type,
            "category_id": category_id,
            "description": description or collector_config_name,
            "data_link_id": int(data_link_id) if data_link_id else 0,
            "bk_app_code": bk_app_code,
            "bkdata_biz_id": bkdata_biz_id,
            "is_display": is_display,
        }
        bkdata_biz_id = bkdata_biz_id or bk_biz_id
        # 判断是否已存在同英文名collector
        if self._pre_check_collector_config_en(model_fields=collector_config_params, bk_biz_id=bkdata_biz_id):
            logger.error(
                "collector_config_name_en {collector_config_name_en} already exists".format(
                    collector_config_name_en=collector_config_name_en
                )
            )
            raise CollectorConfigNameENDuplicateException(
                CollectorConfigNameENDuplicateException.MESSAGE.format(
                    collector_config_name_en=collector_config_name_en
                )
            )
        # 判断是否已存在同bk_data_name, result_table_id
        bk_data_name = build_bk_data_name(bk_biz_id=bkdata_biz_id, collector_config_name_en=collector_config_name_en)
        result_table_id = build_result_table_id(
            bk_biz_id=bkdata_biz_id, collector_config_name_en=collector_config_name_en
        )
        if self._pre_check_bk_data_name(model_fields=collector_config_params, bk_data_name=bk_data_name):
            logger.error(f"bk_data_name {bk_data_name} already exists")
            raise CollectorBkDataNameDuplicateException(
                CollectorBkDataNameDuplicateException.MESSAGE.format(bk_data_name=bk_data_name)
            )
        if self._pre_check_result_table_id(model_fields=collector_config_params, result_table_id=result_table_id):
            logger.error(f"result_table_id {result_table_id} already exists")
            raise CollectorResultTableIDDuplicateException(
                CollectorResultTableIDDuplicateException.MESSAGE.format(result_table_id=result_table_id)
            )

        with transaction.atomic():
            try:
                self.data = CollectorConfig.objects.create(**collector_config_params)
            except IntegrityError:
                logger.warning(f"collector config name duplicate => [{collector_config_name}]")
                raise CollectorConfigNameDuplicateException()

            collector_scenario = CollectorScenario.get_instance(CollectorScenarioEnum.CUSTOM.value)
            self.data.bk_data_id = collector_scenario.update_or_create_data_id(
                bk_data_id=self.data.bk_data_id,
                data_link_id=self.data.data_link_id,
                data_name=build_bk_data_name(bkdata_biz_id, collector_config_name_en),
                description=collector_config_params["description"],
                encoding=META_DATA_ENCODING,
            )
            self.data.save()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.CREATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

        self._authorization_collector(self.data)
        # 创建数据平台data_id
        async_create_bkdata_data_id.delay(self.data.collector_config_id)

        custom_config = get_custom(custom_type)

        # 仅在有集群ID时创建清洗
        if storage_cluster_id:
            from apps.log_databus.handlers.etl import EtlHandler

            etl_handler = EtlHandler.get_instance(self.data.collector_config_id)
            params = {
                "table_id": collector_config_name_en,
                "storage_cluster_id": storage_cluster_id,
                "retention": retention,
                "allocation_min_days": allocation_min_days,
                "storage_replies": storage_replies,
                "es_shards": es_shards,
                "etl_params": custom_config.etl_params,
                "etl_config": custom_config.etl_config,
                "fields": custom_config.fields,
                "sort_fields": sort_fields,
                "target_fields": target_fields,
            }
            if etl_params and fields:
                # 如果传递了清洗参数，则优先使用
                params.update({"etl_params": etl_params, "etl_config": etl_config, "fields": fields})
            self.data.index_set_id = etl_handler.update_or_create(**params)["index_set_id"]
            self.data.save(update_fields=["index_set_id"])

        custom_config.after_hook(self.data)

        ret = {
            "collector_config_id": self.data.collector_config_id,
            "index_set_id": self.data.index_set_id,
            "bk_data_id": self.data.bk_data_id,
        }

        # create custom Log Group
        if custom_type == CustomTypeEnum.OTLP_LOG.value:
            log_group_info = self.create_custom_log_group(self.data)
            ret.update({"bk_data_token": log_group_info.get("bk_data_token")})
        self.send_create_notify(self.data)

        return ret

    def custom_update(
        self,
        collector_config_name=None,
        category_id=None,
        description=None,
        etl_config=None,
        etl_params=None,
        fields=None,
        storage_cluster_id=None,
        retention=7,
        allocation_min_days=0,
        storage_replies=1,
        es_shards=settings.ES_SHARDS,
        is_display=True,
        sort_fields=None,
        target_fields=None,
    ):
        collector_config_update = {
            "collector_config_name": collector_config_name,
            "category_id": category_id,
            "description": description or collector_config_name,
            "is_display": is_display,
        }

        _collector_config_name = self.data.collector_config_name
        bk_data_name = build_bk_data_name(
            bk_biz_id=self.data.get_bk_biz_id(), collector_config_name_en=self.data.collector_config_name_en
        )
        if self.data.bk_data_id and self.data.bk_data_name != bk_data_name:
            TransferApi.modify_data_id({"data_id": self.data.bk_data_id, "data_name": bk_data_name})
            self.data.bk_data_name = bk_data_name
            logger.info(
                "[modify_data_name] bk_data_id=>{}, data_name {}=>{}".format(
                    self.data.bk_data_id, self.data.bk_data_name, bk_data_name
                )
            )

        for key, value in collector_config_update.items():
            setattr(self.data, key, value)
        try:
            self.data.save()
        except IntegrityError:
            logger.warning(f"collector config name duplicate => [{collector_config_name}]")
            raise CollectorConfigNameDuplicateException()

        # collector_config_name更改后更新索引集名称
        if _collector_config_name != self.data.collector_config_name and self.data.index_set_id:
            index_set_name = _("[采集项]") + self.data.collector_config_name
            LogIndexSet.objects.filter(index_set_id=self.data.index_set_id).update(index_set_name=index_set_name)

        custom_config = get_custom(self.data.custom_type)
        if etl_params and fields:
            # 1. 传递了清洗参数，则优先级最高
            etl_params, etl_config, fields = etl_params, etl_config, fields
        elif self.data.etl_config:
            # 2. 如果本身配置过清洗，则直接使用
            collector_detail = self.retrieve()
            # need drop built in field
            collector_detail["fields"] = map_if(
                collector_detail["fields"], if_func=lambda field: not field["is_built_in"]
            )
            etl_params = collector_detail["etl_params"]
            etl_config = collector_detail["etl_config"]
            fields = collector_detail["fields"]
        else:
            # 3. 默认清洗规则，根据自定义类型来
            etl_params = custom_config.etl_params
            etl_config = custom_config.etl_config
            fields = custom_config.fields

        # 仅在传入集群ID时更新
        if storage_cluster_id:
            from apps.log_databus.handlers.etl import EtlHandler

            etl_handler = EtlHandler.get_instance(self.data.collector_config_id)
            etl_params = {
                "table_id": self.data.collector_config_name_en,
                "storage_cluster_id": storage_cluster_id,
                "retention": retention,
                "es_shards": es_shards,
                "allocation_min_days": allocation_min_days,
                "storage_replies": storage_replies,
                "etl_params": etl_params,
                "etl_config": etl_config,
                "fields": fields,
                "sort_fields": sort_fields,
                "target_fields": target_fields,
            }
            etl_handler.update_or_create(**etl_params)

        custom_config.after_hook(self.data)

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.UPDATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

    def pre_check(self, params: dict):
        data = {"allowed": False, "message": _("该数据名已重复")}
        bk_biz_id = params.get("bk_biz_id")
        collector_config_name_en = params.get("collector_config_name_en")

        if self._pre_check_collector_config_en(params, bk_biz_id):
            return data

        bk_data_name = params.get("bk_data_name") or build_bk_data_name(
            bk_biz_id=bk_biz_id, collector_config_name_en=collector_config_name_en
        )
        bk_data = CollectorConfig(bk_data_name=bk_data_name).get_bk_data_by_name()
        if bk_data:
            return data

        result_table_id = params.get("result_table_id") or build_result_table_id(
            bk_biz_id=bk_biz_id, collector_config_name_en=collector_config_name_en
        )
        result_table = CollectorConfig(table_id=result_table_id).get_result_table_by_id()
        if result_table:
            return data

        # 如果采集名不以6-8数字结尾, data.allowed返回True, 反之返回False
        if COLLECTOR_RE.match(collector_config_name_en):
            data.update({"allowed": False, "message": _("采集名不能以6-8位数字结尾")})
        else:
            data.update({"allowed": True, "message": ""})
        return data

    def _pre_check_bk_data_name(self, model_fields: dict, bk_data_name: str):
        if not self.collector_config_id:
            return CollectorConfig(bk_data_name=bk_data_name).get_bk_data_by_name()

        if model_fields["collector_config_name_en"] != self.data.collector_config_name_en:
            return CollectorConfig(bk_data_name=bk_data_name).get_bk_data_by_name()

        return None

    def _pre_check_result_table_id(self, model_fields: dict, result_table_id: str):
        if not self.collector_config_id:
            return CollectorConfig(table_id=result_table_id).get_result_table_by_id()

        if model_fields["collector_config_name_en"] != self.data.collector_config_name_en:
            return CollectorConfig(table_id=result_table_id).get_result_table_by_id()

        return None

    def check_cluster_config(self, bk_biz_id, collector_type, bcs_cluster_id, namespace_list):
        """
        检测共享集群相关配置是否合法
        1. 集群在项目下可见
        2. 不允许配置Node节点日志采集
        3. 不允许设置为all，也不允许为空(namespace设置)
        4. 不允许设置不可见的namespace

        检测虚拟集群相关配置是否合法
        1. 集群在项目下可见
        2. 不允许配置Node节点日志采集
        """
        cluster_info = self.get_cluster_info(bk_biz_id, bcs_cluster_id)

        if cluster_info["is_virtual"]:
            if collector_type == ContainerCollectorType.NODE:
                raise VclusterNodeNotAllowedException()

        if cluster_info["is_shared"]:
            if collector_type == ContainerCollectorType.NODE:
                raise NodeNotAllowedException()

            if not namespace_list:
                raise AllNamespaceNotAllowedException()

            allowed_namespaces = {ns["id"] for ns in self.list_namespace(bk_biz_id, bcs_cluster_id)}

            invalid_namespaces = set(namespace_list) - allowed_namespaces

            if invalid_namespaces:
                raise NamespaceNotValidException(
                    NamespaceNotValidException.MESSAGE.format(namespaces=", ".join(invalid_namespaces))
                )

    def create_container_config(self, data):
        # 使用采集插件补全参数
        collector_plugin_id = data.get("collector_plugin_id")
        if collector_plugin_id:
            from apps.log_databus.handlers.collector_plugin.base import (
                CollectorPluginHandler,
                get_collector_plugin_handler,
            )

            collector_plugin = CollectorPlugin.objects.get(collector_plugin_id=collector_plugin_id)
            plugin_handler: CollectorPluginHandler = get_collector_plugin_handler(
                collector_plugin.etl_processor, collector_plugin_id
            )
            data = plugin_handler.build_instance_params(data)
        data_link_id = int(data.get("data_link_id") or 0)
        data_link_id = get_data_link_id(bk_biz_id=data["bk_biz_id"], data_link_id=data_link_id)
        collector_config_params = {
            "bk_biz_id": data["bk_biz_id"],
            "collector_config_name": data["collector_config_name"],
            "collector_config_name_en": data["collector_config_name_en"],
            "collector_scenario_id": data["collector_scenario_id"],
            "custom_type": CustomTypeEnum.LOG.value,
            "category_id": data["category_id"],
            "description": data["description"] or data["collector_config_name"],
            "data_link_id": int(data_link_id),
            "environment": Environment.CONTAINER,
            "bcs_cluster_id": data["bcs_cluster_id"],
            "add_pod_label": data["add_pod_label"],
            "add_pod_annotation": data["add_pod_annotation"],
            "extra_labels": data["extra_labels"],
            "yaml_config_enabled": data["yaml_config_enabled"],
            "yaml_config": data["yaml_config"],
            "bkdata_biz_id": data.get("bkdata_biz_id"),
            "collector_plugin_id": collector_plugin_id,
            "is_display": data.get("is_display", True),
            "etl_processor": data.get("etl_processor", ETLProcessorChoices.TRANSFER.value),
        }
        bkdata_biz_id = data.get("bkdata_biz_id") or data["bk_biz_id"]
        if self._pre_check_collector_config_en(model_fields=collector_config_params, bk_biz_id=bkdata_biz_id):
            logger.error(
                "collector_config_name_en {collector_config_name_en} already exists".format(
                    collector_config_name_en=data["collector_config_name_en"]
                )
            )
            raise CollectorConfigNameENDuplicateException(
                CollectorConfigNameENDuplicateException.MESSAGE.format(
                    collector_config_name_en=data["collector_config_name_en"]
                )
            )
        # 判断是否已存在同bk_data_name, result_table_id
        bk_data_name = build_bk_data_name(
            bk_biz_id=bkdata_biz_id, collector_config_name_en=data["collector_config_name_en"]
        )
        result_table_id = build_result_table_id(
            bk_biz_id=bkdata_biz_id, collector_config_name_en=data["collector_config_name_en"]
        )
        if self._pre_check_bk_data_name(model_fields=collector_config_params, bk_data_name=bk_data_name):
            logger.error(f"bk_data_name {bk_data_name} already exists")
            raise CollectorBkDataNameDuplicateException(
                CollectorBkDataNameDuplicateException.MESSAGE.format(bk_data_name=bk_data_name)
            )
        if self._pre_check_result_table_id(model_fields=collector_config_params, result_table_id=result_table_id):
            logger.error(f"result_table_id {result_table_id} already exists")
            raise CollectorResultTableIDDuplicateException(
                CollectorResultTableIDDuplicateException.MESSAGE.format(result_table_id=result_table_id)
            )

        with transaction.atomic():
            try:
                self.data = CollectorConfig.objects.create(**collector_config_params)
            except IntegrityError:
                logger.warning(f"collector config name duplicate => [{data['collector_config_name']}]")
                raise CollectorConfigNameDuplicateException()

            if self.data.yaml_config_enabled:
                # yaml 模式，先反序列化解出来，再保存
                result = self.validate_container_config_yaml(
                    data["bk_biz_id"], data["bcs_cluster_id"], self.data.yaml_config
                )
                if not result["parse_status"]:
                    raise ContainerCollectConfigValidateYamlException()
                container_configs = result["parse_result"]["configs"]
            else:
                # 效验共享集群命名空间是否在允许的范围
                for config in data["configs"]:
                    if config.get("namespaces"):
                        self.check_cluster_config(
                            bk_biz_id=data["bk_biz_id"],
                            collector_type=config["collector_type"],
                            bcs_cluster_id=data["bcs_cluster_id"],
                            namespace_list=config["namespaces"],
                        )

                # 原生模式，直接通过结构化数据生成
                container_configs = data["configs"]

            ContainerCollectorConfig.objects.bulk_create(
                ContainerCollectorConfig(
                    collector_config_id=self.data.collector_config_id,
                    collector_type=config["collector_type"],
                    namespaces=config["namespaces"],
                    namespaces_exclude=config["namespaces_exclude"],
                    any_namespace=not any([config["namespaces"], config["namespaces_exclude"]]),
                    data_encoding=config["data_encoding"],
                    params=config["params"],
                    workload_type=config["container"]["workload_type"],
                    workload_name=config["container"]["workload_name"],
                    container_name=config["container"]["container_name"],
                    container_name_exclude=config["container"]["container_name_exclude"],
                    match_labels=config["label_selector"]["match_labels"],
                    match_expressions=config["label_selector"]["match_expressions"],
                    match_annotations=config["annotation_selector"]["match_annotations"],
                    all_container=not any(
                        [
                            config["container"]["workload_type"],
                            config["container"]["workload_name"],
                            config["container"]["container_name"],
                            config["container"]["container_name_exclude"],
                            config["label_selector"]["match_labels"],
                            config["label_selector"]["match_expressions"],
                            config["annotation_selector"]["match_annotations"],
                        ]
                    ),
                    # yaml 原始配置，如果启用了yaml，则把解析后的原始配置保存下来用于下发
                    raw_config=config.get("raw_config") if self.data.yaml_config_enabled else None,
                )
                for config in container_configs
            )

            collector_scenario = CollectorScenario.get_instance(CollectorScenarioEnum.CUSTOM.value)
            self.data.bk_data_id = collector_scenario.update_or_create_data_id(
                bk_data_id=self.data.bk_data_id,
                data_link_id=self.data.data_link_id,
                data_name=build_bk_data_name(self.data.get_bk_biz_id(), data["collector_config_name_en"]),
                description=collector_config_params["description"],
                encoding=META_DATA_ENCODING,
            )
            self.data.task_id_list = list(
                ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id).values_list(
                    "id", flat=True
                )
            )

            self.data.save()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.CREATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

        self._authorization_collector(self.data)
        # 创建数据平台data_id
        # 兼容平台账号
        async_create_bkdata_data_id.delay(self.data.collector_config_id, data.get("platform_username"))

        container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.data.collector_config_id)
        for config in container_configs:
            self.create_container_release(config)
        return {
            "collector_config_id": self.data.collector_config_id,
            "collector_config_name": self.data.collector_config_name,
            "bk_data_id": self.data.bk_data_id,
            "subscription_id": self.data.subscription_id,
            "task_id_list": self.data.task_id_list,
        }

    def update_container_config(self, data):
        bk_biz_id = data["bk_biz_id"]
        collector_config_update = {
            "collector_config_name": data["collector_config_name"],
            "description": data["description"] or data["collector_config_name"],
            "environment": Environment.CONTAINER,
            "collector_scenario_id": data["collector_scenario_id"],
            "bcs_cluster_id": data["bcs_cluster_id"],
            "add_pod_label": data["add_pod_label"],
            "add_pod_annotation": data["add_pod_annotation"],
            "extra_labels": data["extra_labels"],
            "yaml_config_enabled": data["yaml_config_enabled"],
            "yaml_config": data["yaml_config"],
        }

        if data["yaml_config_enabled"]:
            # yaml 模式，先反序列化解出来，覆盖到config字段上面
            validate_result = self.validate_container_config_yaml(
                bk_biz_id, data["bcs_cluster_id"], data["yaml_config"]
            )
            if not validate_result["parse_status"]:
                raise ContainerCollectConfigValidateYamlException()
            data["configs"] = validate_result["parse_result"]["configs"]

        # 效验共享集群命名空间是否在允许的范围
        for config in data["configs"]:
            if config.get("namespaces"):
                self.check_cluster_config(
                    bk_biz_id=bk_biz_id,
                    collector_type=config["collector_type"],
                    bcs_cluster_id=data["bcs_cluster_id"],
                    namespace_list=config["namespaces"],
                )

        _collector_config_name = self.data.collector_config_name
        for key, value in collector_config_update.items():
            setattr(self.data, key, value)

        try:
            self.data.save()
        except IntegrityError:
            logger.warning(f"collector config name duplicate => [{data['collector_config_name']}]")
            raise CollectorConfigNameDuplicateException()

        # collector_config_name更改后更新索引集名称
        if _collector_config_name != self.data.collector_config_name and self.data.index_set_id:
            index_set_name = _("[采集项]") + self.data.collector_config_name
            LogIndexSet.objects.filter(index_set_id=self.data.index_set_id).update(index_set_name=index_set_name)

        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.UPDATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)
        self.compare_config(data=data, collector_config_id=self.data.collector_config_id)

        self.data.task_id_list = list(
            ContainerCollectorConfig.objects.filter(collector_config_id=self.collector_config_id).values_list(
                "id", flat=True
            )
        )
        self.data.save()

        return {
            "collector_config_id": self.data.collector_config_id,
            "index_set_id": self.data.index_set_id,
            "bk_data_id": self.data.bk_data_id,
        }

    @classmethod
    def list_bcs_collector_without_rule(cls, bcs_cluster_id: str, bk_biz_id: int):
        """
        该函数是为了获取容器采集项, 但是不是通过BCS规则创建的采集项
        """
        # 通用函数, 获取非BCS创建的容器采集项, 以及对应容器采集的map
        queryset = CollectorConfig.objects.filter(
            rule_id=0,
            environment=Environment.CONTAINER,
            bk_biz_id=bk_biz_id,
            bcs_cluster_id=bcs_cluster_id,
            # 过滤掉未完成的采集项, 因为未完成的采集项table_id会为空
            table_id__isnull=False,
        )
        collectors = queryset.all()
        # 获取采集项对应的容器采集配置
        container_collector_configs = ContainerCollectorConfig.objects.filter(
            collector_config_id__in=list(collectors.values_list("collector_config_id", flat=True)),
            collector_type__in=[ContainerCollectorType.CONTAINER, ContainerCollectorType.STDOUT],
        ).all()
        container_config_map: dict[int, ContainerCollectorConfig] = {
            c.collector_config_id: c for c in container_collector_configs
        }
        return [
            cls.format_bcs_container_config(
                collector_config=collector, container_config=container_config_map[collector.collector_config_id]
            )
            for collector in collectors
            if collector.collector_config_id in container_config_map
        ]

    @classmethod
    def format_bcs_container_config(
        cls, collector_config: CollectorConfig, container_config: ContainerCollectorConfig
    ) -> dict[str, Any]:
        enable_stdout = container_config.collector_type == ContainerCollectorType.STDOUT
        return {
            "created_by": collector_config.created_by,
            "updated_by": collector_config.updated_by,
            "created_at": collector_config.created_at,
            "updated_at": collector_config.updated_at,
            "rule_id": collector_config.rule_id,
            "collector_config_name": collector_config.collector_config_name,
            "bk_biz_id": collector_config.bk_biz_id,
            "description": collector_config.description,
            "collector_config_name_en": collector_config.collector_config_name_en,
            "environment": collector_config.environment,
            "bcs_cluster_id": collector_config.bcs_cluster_id,
            "extra_labels": collector_config.extra_labels,
            "add_pod_label": collector_config.add_pod_label,
            "rule_file_index_set_id": None,
            "rule_std_index_set_id": None,
            "file_index_set_id": collector_config.index_set_id
            if not enable_stdout
            else None,  # TODO: 兼容代码4.8需删除
            "std_index_set_id": collector_config.index_set_id if enable_stdout else None,  # TODO: 兼容代码4.8需删除
            "container_config": [
                {
                    "id": container_config.id,
                    "bk_data_id": collector_config.bk_data_id if not enable_stdout else None,
                    "bkdata_data_id": collector_config.bkdata_data_id if not enable_stdout else None,
                    "namespaces": container_config.namespaces,
                    "any_namespace": container_config.any_namespace,
                    "data_encoding": container_config.data_encoding,
                    "params": container_config.params,
                    "container": {
                        "workload_type": container_config.workload_type,
                        "workload_name": container_config.workload_name,
                        "container_name": container_config.container_name,
                    },
                    "label_selector": {
                        "match_labels": container_config.match_labels,
                        "match_expressions": container_config.match_expressions,
                    },
                    "annotation_selector": {
                        "match_annotations": container_config.match_annotations,
                    },
                    "all_container": container_config.all_container,
                    "status": container_config.status,
                    "status_detail": container_config.status_detail,
                    "enable_stdout": enable_stdout,
                    "stdout_conf": {
                        "bk_data_id": collector_config.bk_data_id if enable_stdout else None,
                        "bkdata_data_id": collector_config.bkdata_data_id if enable_stdout else None,
                    },
                }
            ],
        }

    def list_bcs_collector(self, bcs_cluster_id, bk_biz_id=None, bk_app_code="bk_bcs"):
        queryset = CollectorConfig.objects.filter(bcs_cluster_id=bcs_cluster_id, bk_app_code=bk_app_code)
        if bk_biz_id:
            queryset = queryset.filter(bk_biz_id=bk_biz_id)
        collectors = queryset.exclude(bk_app_code="bk_log_search").order_by("-updated_at")
        rule_dict = {}
        if not collectors:
            return []

        def is_path_collector_config(collector_config_name_en: str):
            return collector_config_name_en.endswith("_path")

        for collector in collectors:
            if not collector.rule_id:
                continue
            if collector.rule_id not in rule_dict:
                rule_dict[collector.rule_id] = {
                    "path_collector_config": CollectorConfig(),
                    "std_collector_config": CollectorConfig(),
                }
            if is_path_collector_config(collector.collector_config_name_en):
                rule_dict[collector.rule_id]["path_collector_config"] = collector
            else:
                rule_dict[collector.rule_id]["std_collector_config"] = collector

        path_container_config_list = ContainerCollectorConfig.objects.filter(
            collector_config_id__in=[rule["path_collector_config"].collector_config_id for _, rule in rule_dict.items()]
        ).order_by("-updated_at")
        std_container_config_list = ContainerCollectorConfig.objects.filter(
            collector_config_id__in=[rule["std_collector_config"].collector_config_id for _, rule in rule_dict.items()]
        ).order_by("-updated_at")

        path_container_config_dict = defaultdict(list)
        std_container_config_dict = defaultdict(list)
        for path_container_config in path_container_config_list:
            path_container_config_dict[path_container_config.collector_config_id].append(path_container_config)
        for std_container_config in std_container_config_list:
            std_container_config_dict[std_container_config.parent_container_config_id].append(std_container_config)
            std_container_config_dict[std_container_config.collector_config_id].append(std_container_config)

        result = []
        for rule_id, collector in rule_dict.items():
            collector_config_name_en = collector["path_collector_config"].collector_config_name_en
            collector_config_name = collector["path_collector_config"].collector_config_name
            if not collector_config_name_en:
                collector_config_name_en = collector["std_collector_config"].collector_config_name_en
                collector_config_name = collector["std_collector_config"].collector_config_name
            elif collector_config_name_en.startswith("bcs_k8s_"):
                # 模式: bcs_k8s_12345_your_name_std
                collector_config_name_en = collector_config_name_en.rsplit("_", 1)[0].split("_", 3)[3]
            else:
                # 模式: bcs_your_name_std
                collector_config_name_en = collector_config_name_en.rsplit("_", 1)[0].split("_", 1)[1]

            # 解析采集中文名称，若不符合BCS默认格式，则传递原采集名
            if collector_config_name:
                try:
                    collector_config_name = collector_config_name.rsplit("_", 1)[0].split("_", 1)[1]
                except Exception:  # pylint: disable=broad-except
                    collector_config_name = collector_config_name
            else:
                collector_config_name = ""

            is_collector_deleted = not bool(
                collector["std_collector_config"].index_set_id or collector["path_collector_config"].index_set_id
            )

            rule = {
                "rule_id": rule_id,
                "collector_config_name": collector_config_name,
                "bk_biz_id": collector["path_collector_config"].bk_biz_id,
                "description": collector["path_collector_config"].description,
                "collector_config_name_en": collector_config_name_en,
                "environment": collector["path_collector_config"].environment,
                "bcs_cluster_id": collector["path_collector_config"].bcs_cluster_id,
                "extra_labels": collector["path_collector_config"].extra_labels,
                "add_pod_label": collector["path_collector_config"].add_pod_label,
                "rule_file_index_set_id": collector["path_collector_config"].index_set_id,
                "rule_std_index_set_id": collector["std_collector_config"].index_set_id,
                "file_index_set_id": collector["path_collector_config"].index_set_id,  # TODO: 兼容代码4.8需删除
                "std_index_set_id": collector["std_collector_config"].index_set_id,  # TODO: 兼容代码4.8需删除
                "is_std_deleted": is_collector_deleted,
                "is_file_deleted": is_collector_deleted,
                "container_config": [],
            }

            collector_config_id = (
                collector["path_collector_config"].collector_config_id
                or collector["std_collector_config"].collector_config_id
            )
            container_configs = path_container_config_dict.get(collector_config_id) or std_container_config_dict.get(
                collector_config_id
            )

            if not container_configs:
                result.append(rule)
                continue
            for container_config in container_configs:
                rule["container_config"].append(
                    {
                        "id": container_config.id,
                        "bk_data_id": collector["path_collector_config"].bk_data_id,
                        "bkdata_data_id": collector["path_collector_config"].bkdata_data_id,
                        "namespaces": container_config.namespaces,
                        "any_namespace": container_config.any_namespace,
                        "data_encoding": container_config.data_encoding,
                        "params": container_config.params,
                        "container": {
                            "workload_type": container_config.workload_type,
                            "workload_name": container_config.workload_name,
                            "container_name": container_config.container_name,
                        },
                        "label_selector": {
                            "match_labels": container_config.match_labels,
                            "match_expressions": container_config.match_expressions,
                        },
                        "annotation_selector": {
                            "match_annotations": container_config.match_annotations,
                        },
                        "all_container": container_config.all_container,
                        "status": container_config.status,
                        "status_detail": container_config.status_detail,
                        "enable_stdout": collector_config_id in std_container_config_dict,
                        "stdout_conf": {
                            "bk_data_id": collector["std_collector_config"].bk_data_id,
                            "bkdata_data_id": collector["std_collector_config"].bkdata_data_id,
                        },
                    }
                )
            result.append(rule)
        return result

    def get_bcs_collector_storage(self, bcs_cluster_id, bk_biz_id=None):
        bcs_storage_config = BcsStorageClusterConfig.objects.filter(
            bk_biz_id=bk_biz_id, bcs_cluster_id=bcs_cluster_id
        ).first()
        toggle = FeatureToggleObject.toggle(BCS_COLLECTOR)
        conf = toggle.feature_config if toggle else {}
        # 优先使用传的集群ID, 传的集群ID和bcs业务指定存储集群都不存在时, 使用第一个默认集群
        storage_cluster_id = (
            bcs_storage_config.storage_cluster_id if bcs_storage_config else conf.get("storage_cluster_id")
        )
        if not storage_cluster_id:
            es_clusters = TransferApi.get_cluster_info({"cluster_type": STORAGE_CLUSTER_TYPE, "no_request": True})
            for es in es_clusters:
                if es["cluster_config"]["is_default_cluster"]:
                    storage_cluster_id = es["cluster_config"]["cluster_id"]

        return storage_cluster_id

    @transaction.atomic
    def create_bcs_container_config(self, data, bk_app_code="bk_bcs"):
        conf = self.get_bcs_config(
            bk_biz_id=data["bk_biz_id"],
            bcs_cluster_id=data["bcs_cluster_id"],
            storage_cluster_id=data.get("storage_cluster_id"),
        )
        bcs_collector_config_name = self.generate_collector_config_name(
            bcs_cluster_id=data["bcs_cluster_id"],
            collector_config_name=data["collector_config_name"],
            collector_config_name_en=data["collector_config_name_en"],
        )
        bcs_rule = BcsRule.objects.create(rule_name=data["collector_config_name"], bcs_project_id=data["project_id"])

        # 默认设置为空,做为一个标识
        path_collector_config = std_collector_config = ""
        parent_container_config_id = 0
        # 注入索引集标签
        tag_id = IndexSetTag.get_tag_id(data["bcs_cluster_id"])
        is_send_create_notify = False
        for config in data["config"]:
            if config["paths"]:
                # 创建路径采集项
                path_collector_config = self.create_bcs_collector(
                    {
                        "bk_biz_id": data["bk_biz_id"],
                        "collector_config_name": bcs_collector_config_name["bcs_path_collector"][
                            "collector_config_name"
                        ],
                        "collector_config_name_en": bcs_collector_config_name["bcs_path_collector"][
                            "collector_config_name_en"
                        ],
                        "collector_scenario_id": CollectorScenarioEnum.ROW.value,
                        "custom_type": data["custom_type"],
                        "category_id": data["category_id"],
                        "description": data["description"],
                        "data_link_id": int(conf["data_link_id"]),
                        "bk_app_code": bk_app_code,
                        "environment": Environment.CONTAINER,
                        "bcs_cluster_id": data["bcs_cluster_id"],
                        "add_pod_label": data["add_pod_label"],
                        "extra_labels": data["extra_labels"],
                        "rule_id": bcs_rule.id,
                    },
                    conf=conf,
                    async_bkdata=False,
                )
                is_send_create_notify = True
                # 注入索引集标签
                IndexSetHandler(path_collector_config.index_set_id).add_tag(tag_id=tag_id)

            if config["enable_stdout"]:
                # 创建标准输出采集项
                std_collector_config = self.create_bcs_collector(
                    {
                        "bk_biz_id": data["bk_biz_id"],
                        "collector_config_name": bcs_collector_config_name["bcs_std_collector"][
                            "collector_config_name"
                        ],
                        "collector_config_name_en": bcs_collector_config_name["bcs_std_collector"][
                            "collector_config_name_en"
                        ],
                        "collector_scenario_id": CollectorScenarioEnum.ROW.value,
                        "custom_type": data["custom_type"],
                        "category_id": data["category_id"],
                        "description": data["description"],
                        "data_link_id": int(conf["data_link_id"]),
                        "bk_app_code": bk_app_code,
                        "environment": Environment.CONTAINER,
                        "bcs_cluster_id": data["bcs_cluster_id"],
                        "add_pod_label": data["add_pod_label"],
                        "extra_labels": data["extra_labels"],
                        "rule_id": bcs_rule.id,
                    },
                    conf=conf,
                    async_bkdata=False,
                )
                # 注入索引集标签
                IndexSetHandler(std_collector_config.index_set_id).add_tag(tag_id=tag_id)
                # 获取父配置id
                collector_config_obj = CollectorConfig.objects.filter(
                    rule_id=bcs_rule.id,
                    collector_config_name_en=bcs_collector_config_name["bcs_path_collector"][
                        "collector_config_name_en"
                    ],
                ).first()
                if collector_config_obj:
                    parent_container_config_id = collector_config_obj.collector_config_id

        container_collector_config_list = []
        for config in data["config"]:
            workload_type = config["container"].get("workload_type", "")
            workload_name = config["container"].get("workload_name", "")
            container_name = config["container"].get("container_name", "")
            match_labels = config["label_selector"].get("match_labels", [])
            match_expressions = config["label_selector"].get("match_expressions", [])
            match_annotations = config["annotation_selector"].get("match_annotations", [])

            is_all_container = not any(
                [workload_type, workload_name, container_name, match_labels, match_expressions, match_annotations]
            )

            if config["paths"]:
                # 配置了文件路径才需要下发路径采集
                container_collector_config_list.append(
                    ContainerCollectorConfig(
                        collector_config_id=path_collector_config.collector_config_id,
                        collector_type=ContainerCollectorType.CONTAINER,
                        namespaces=config["namespaces"],
                        any_namespace=not config["namespaces"],
                        data_encoding=config["data_encoding"],
                        params={
                            "paths": config["paths"],
                            "conditions": config["conditions"]
                            if config.get("conditions")
                            else {"type": "match", "match_type": "include", "match_content": ""},
                            **config.get("multiline", {}),
                        },
                        workload_type=workload_type,
                        workload_name=workload_name,
                        container_name=container_name,
                        match_labels=match_labels,
                        match_expressions=match_expressions,
                        match_annotations=match_annotations,
                        all_container=is_all_container,
                        rule_id=bcs_rule.id,
                    )
                )

            if config["enable_stdout"]:
                container_collector_config_list.append(
                    ContainerCollectorConfig(
                        collector_config_id=std_collector_config.collector_config_id,
                        collector_type=ContainerCollectorType.STDOUT,
                        namespaces=config["namespaces"],
                        any_namespace=not config["namespaces"],
                        data_encoding=config["data_encoding"],
                        params={
                            "paths": [],
                            "conditions": config["conditions"]
                            if config.get("conditions")
                            else {"type": "match", "match_type": "include", "match_content": ""},
                            **config.get("multiline", {}),
                        },
                        workload_type=workload_type,
                        workload_name=workload_name,
                        container_name=container_name,
                        match_labels=match_labels,
                        match_expressions=match_expressions,
                        match_annotations=match_annotations,
                        all_container=is_all_container,
                        rule_id=bcs_rule.id,
                        parent_container_config_id=parent_container_config_id,
                    )
                )

        ContainerCollectorConfig.objects.bulk_create(container_collector_config_list)

        if is_send_create_notify:
            self.send_create_notify(path_collector_config)

        return {
            "rule_id": bcs_rule.id,
            "rule_file_index_set_id": path_collector_config.index_set_id if path_collector_config else 0,
            "rule_file_collector_config_id": path_collector_config.collector_config_id if path_collector_config else 0,
            "rule_std_index_set_id": std_collector_config.index_set_id if std_collector_config else 0,
            "rule_std_collector_config_id": std_collector_config.collector_config_id if std_collector_config else 0,
            "file_index_set_id": path_collector_config.index_set_id
            if path_collector_config
            else 0,  # TODO: 兼容代码4.8需删除
            "std_index_set_id": std_collector_config.index_set_id
            if std_collector_config
            else 0,  # TODO: 兼容代码4.8需删除
            "bk_data_id": path_collector_config.bk_data_id if path_collector_config else 0,
            "stdout_conf": {"bk_data_id": std_collector_config.bk_data_id if std_collector_config else 0},
        }

    def sync_bcs_container_task(self, data: dict[str, Any]):
        """
        同步bcs容器采集项任务
        需要在create_bcs_container_config函数执行之后运行
        因为create_bcs_container_config函数在事务里, 异步任务可能会执行失败, 需要在事务完成之后单独执行
        """
        file_collector_config_id = data["rule_file_collector_config_id"]
        std_collector_config_id = data["rule_std_collector_config_id"]
        for collector_config_id in [file_collector_config_id, std_collector_config_id]:
            if not collector_config_id:
                continue
            collector_config = CollectorConfig.objects.filter(
                collector_config_id=collector_config_id,
            ).first()
            if not collector_config:
                continue
            container_config = ContainerCollectorConfig.objects.filter(
                collector_config_id=collector_config_id,
            ).first()
            if not container_config:
                continue
            self.deal_self_call(
                collector_config_id=collector_config.collector_config_id,
                collector=collector_config,
                func=self.create_container_release,
                container_config=container_config,
            )

    @staticmethod
    def sync_bcs_container_bkdata_id(data: dict[str, Any]):
        """同步bcs容器采集项bkdata_id"""
        if data["rule_file_collector_config_id"]:
            async_create_bkdata_data_id.delay(data["rule_file_collector_config_id"])
        if data["rule_std_collector_config_id"]:
            async_create_bkdata_data_id.delay(data["rule_std_collector_config_id"])

    @classmethod
    def generate_collector_config_name(cls, bcs_cluster_id, collector_config_name, collector_config_name_en):
        lower_cluster_id = convert_lower_cluster_id(bcs_cluster_id)
        return {
            "bcs_path_collector": {
                "collector_config_name": f"{collector_config_name}_path",
                "collector_config_name_en": f"{lower_cluster_id}_{collector_config_name_en}_path",
            },
            "bcs_std_collector": {
                "collector_config_name": f"{collector_config_name}_std",
                "collector_config_name_en": f"{lower_cluster_id}_{collector_config_name_en}_std",
            },
        }

    def create_bcs_collector(self, collector_config_params, conf, async_bkdata: bool = True):
        self.check_collector_config(collector_config_params=collector_config_params)
        try:
            self.data = CollectorConfig.objects.create(**collector_config_params)
        except IntegrityError:
            logger.warning(f"collector config name duplicate => [{collector_config_params['collector_config_name']}]")
            raise CollectorConfigNameDuplicateException()
        collector_scenario = CollectorScenario.get_instance(CollectorScenarioEnum.CUSTOM.value)
        self.data.bk_data_id = collector_scenario.update_or_create_data_id(
            bk_data_id=self.data.bk_data_id,
            data_link_id=self.data.data_link_id,
            data_name=build_bk_data_name(self.data.bk_biz_id, collector_config_params["collector_config_name_en"]),
            description=collector_config_params["description"]
            if collector_config_params["description"]
            else collector_config_params["collector_config_name_en"],
            encoding=META_DATA_ENCODING,
        )
        self.data.save()

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.CREATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

        self._authorization_collector(self.data)
        # 创建数据平台data_id
        if async_bkdata:
            async_create_bkdata_data_id.delay(self.data.collector_config_id)

        custom_config = get_custom(collector_config_params["custom_type"])
        from apps.log_databus.handlers.etl import EtlHandler

        etl_handler = EtlHandler(self.data.collector_config_id)
        etl_params = {
            "table_id": collector_config_params["collector_config_name_en"],
            "storage_cluster_id": conf["storage_cluster_id"],
            "retention": DEFAULT_RETENTION,
            "allocation_min_days": 0,
            "storage_replies": 0,
            "etl_params": custom_config.etl_params,
            "etl_config": custom_config.etl_config,
            "fields": custom_config.fields,
        }
        etl_result = etl_handler.update_or_create(**etl_params)
        self.data.index_set_id = etl_result["index_set_id"]
        self.data.table_id = etl_result["table_id"]
        custom_config.after_hook(self.data)
        return self.data

    def check_collector_config(self, collector_config_params):
        if self._pre_check_collector_config_en(
            model_fields=collector_config_params, bk_biz_id=collector_config_params["bk_biz_id"]
        ):
            logger.error(
                "collector_config_name_en {collector_config_name_en} already exists".format(
                    collector_config_name_en=collector_config_params["collector_config_name_en"]
                )
            )
            raise CollectorConfigNameENDuplicateException(
                CollectorConfigNameENDuplicateException.MESSAGE.format(
                    collector_config_name_en=collector_config_params["collector_config_name_en"]
                )
            )
        # 判断是否已存在同bk_data_name, result_table_id
        bk_data_name = build_bk_data_name(
            bk_biz_id=collector_config_params["bk_biz_id"],
            collector_config_name_en=collector_config_params["collector_config_name_en"],
        )
        result_table_id = build_result_table_id(
            bk_biz_id=collector_config_params["bk_biz_id"],
            collector_config_name_en=collector_config_params["collector_config_name_en"],
        )
        if self._pre_check_bk_data_name(model_fields=collector_config_params, bk_data_name=bk_data_name):
            logger.error(f"bk_data_name {bk_data_name} already exists")
            raise CollectorBkDataNameDuplicateException(
                CollectorBkDataNameDuplicateException.MESSAGE.format(bk_data_name=bk_data_name)
            )
        if self._pre_check_result_table_id(model_fields=collector_config_params, result_table_id=result_table_id):
            logger.error(f"result_table_id {result_table_id} already exists")
            raise CollectorResultTableIDDuplicateException(
                CollectorResultTableIDDuplicateException.MESSAGE.format(result_table_id=result_table_id)
            )

    @transaction.atomic
    def update_bcs_container_config(self, data, rule_id, bk_app_code="bk_bcs"):
        conf = self.get_bcs_config(
            bk_biz_id=data["bk_biz_id"],
            bcs_cluster_id=data["bcs_cluster_id"],
            storage_cluster_id=data.get("storage_cluster_id"),
        )
        bcs_collector_config_name = self.generate_collector_config_name(
            bcs_cluster_id=data["bcs_cluster_id"],
            collector_config_name=data["collector_config_name"],
            collector_config_name_en=data["collector_config_name_en"],
        )
        bcs_path_collector_config_name_en = bcs_collector_config_name["bcs_path_collector"]["collector_config_name_en"]
        bcs_std_collector_config_name_en = bcs_collector_config_name["bcs_std_collector"]["collector_config_name_en"]

        # 默认设置为空,做为一个标识
        path_collector = std_collector = None
        path_collector_config = std_collector_config = None
        # 注入索引集标签
        tag_id = IndexSetTag.get_tag_id(data["bcs_cluster_id"])
        is_send_path_create_notify = is_send_std_create_notify = False
        # 容器配置是否创建标识
        is_exist_bcs_path = False
        is_exist_bcs_std = False
        for config in data["config"]:
            collector_config_name_en_list = CollectorConfig.objects.filter(
                rule_id=rule_id,
                collector_config_name_en__in=[bcs_path_collector_config_name_en, bcs_std_collector_config_name_en],
            ).values_list("collector_config_name_en", flat=True)

            for collector_config_name_en in collector_config_name_en_list:
                if collector_config_name_en.endswith("_path"):
                    is_exist_bcs_path = True
                elif collector_config_name_en.endswith("_std"):
                    is_exist_bcs_std = True

            # 如果还没有创建容器配置，那么当config["paths"]或config["enable_stdout"]存在时需要创建容器配置
            if config["paths"] and not is_exist_bcs_path:
                # 创建路径采集项
                path_collector_config = self.create_bcs_collector(
                    {
                        "bk_biz_id": data["bk_biz_id"],
                        "collector_config_name": bcs_collector_config_name["bcs_path_collector"][
                            "collector_config_name"
                        ],
                        "collector_config_name_en": bcs_collector_config_name["bcs_path_collector"][
                            "collector_config_name_en"
                        ],
                        "collector_scenario_id": CollectorScenarioEnum.ROW.value,
                        "custom_type": data["custom_type"],
                        "category_id": data["category_id"],
                        "description": data["description"],
                        "data_link_id": int(conf["data_link_id"]),
                        "bk_app_code": bk_app_code,
                        "environment": Environment.CONTAINER,
                        "bcs_cluster_id": data["bcs_cluster_id"],
                        "add_pod_label": data["add_pod_label"],
                        "extra_labels": data["extra_labels"],
                        "rule_id": rule_id,
                    },
                    conf=conf,
                    async_bkdata=False,
                )
                is_send_path_create_notify = True
                # 注入索引集标签
                IndexSetHandler(path_collector_config.index_set_id).add_tag(tag_id=tag_id)
            if config["enable_stdout"] and not is_exist_bcs_std:
                # 创建标准输出采集项
                std_collector_config = self.create_bcs_collector(
                    {
                        "bk_biz_id": data["bk_biz_id"],
                        "collector_config_name": bcs_collector_config_name["bcs_std_collector"][
                            "collector_config_name"
                        ],
                        "collector_config_name_en": bcs_collector_config_name["bcs_std_collector"][
                            "collector_config_name_en"
                        ],
                        "collector_scenario_id": CollectorScenarioEnum.ROW.value,
                        "custom_type": data["custom_type"],
                        "category_id": data["category_id"],
                        "description": data["description"],
                        "data_link_id": int(conf["data_link_id"]),
                        "bk_app_code": bk_app_code,
                        "environment": Environment.CONTAINER,
                        "bcs_cluster_id": data["bcs_cluster_id"],
                        "add_pod_label": data["add_pod_label"],
                        "extra_labels": data["extra_labels"],
                        "rule_id": rule_id,
                    },
                    conf=conf,
                    async_bkdata=False,
                )
                # 注入索引集标签
                is_send_std_create_notify = True
                IndexSetHandler(std_collector_config.index_set_id).add_tag(tag_id=tag_id)

        collectors = CollectorConfig.objects.filter(rule_id=rule_id)
        if not collectors:
            raise RuleCollectorException(RuleCollectorException.MESSAGE.format(rule_id=rule_id))
        for collector in collectors:
            if collector.collector_config_name_en.endswith("_path"):
                collector.description = data["description"]
                collector.bcs_cluster_id = data["bcs_cluster_id"]
                collector.add_pod_label = data["add_pod_label"]
                collector.extra_labels = data["extra_labels"]
                collector.save()
                path_collector = collector
            if collector.collector_config_name_en.endswith("_std"):
                collector.description = data["description"]
                collector.bcs_cluster_id = data["bcs_cluster_id"]
                collector.add_pod_label = data["add_pod_label"]
                collector.extra_labels = data["extra_labels"]
                collector.save()
                std_collector = collector

        path_container_config, std_container_config = self.get_container_configs(
            data["config"], path_collector=path_collector, rule_id=rule_id
        )
        if path_collector:
            self.deal_self_call(
                collector_config_id=path_collector.collector_config_id,
                collector=path_collector,
                func=self.compare_config,
                **{"data": {"configs": path_container_config}},
            )
        if std_collector:
            self.deal_self_call(
                collector_config_id=std_collector.collector_config_id,
                collector=std_collector,
                func=self.compare_config,
                **{"data": {"configs": std_container_config}},
            )

        if is_send_path_create_notify:
            self.send_create_notify(path_collector_config)

        if is_send_std_create_notify:
            self.send_create_notify(std_collector_config)

        return {
            "rule_id": rule_id,
            "rule_file_index_set_id": path_collector.index_set_id if path_collector else 0,
            "rule_std_index_set_id": std_collector.index_set_id if std_collector else 0,
            "file_index_set_id": path_collector.index_set_id if path_collector else 0,  # TODO: 兼容代码4.8需删除
            "std_index_set_id": std_collector.index_set_id if std_collector else 0,  # TODO: 兼容代码4.8需删除
            "bk_data_id": path_collector.bk_data_id if path_collector else 0,
            "stdout_conf": {"bk_data_id": std_collector.bk_data_id if std_collector else 0},
        }

    def deal_self_call(self, **kwargs):
        """
        collector_config_id, collector, func 必传
        """
        self.collector_config_id = kwargs["collector_config_id"]
        self.data = kwargs["collector"]
        func = kwargs["func"]
        return func(**kwargs)

    @classmethod
    def get_container_configs(cls, config, path_collector, rule_id):
        path_container_config = []
        std_container_config = []
        for conf in config:
            if conf["paths"]:
                path_container_config.append(
                    {
                        "namespaces": conf["namespaces"],
                        "namespaces_exclude": conf["namespaces_exclude"],
                        "any_namespace": not conf["namespaces"],
                        "data_encoding": conf["data_encoding"],
                        "params": {
                            "paths": conf["paths"],
                            "conditions": conf["conditions"]
                            if conf.get("conditions")
                            else {"type": "match", "match_type": "include", "match_content": ""},
                            **conf.get("multiline", {}),
                        },
                        "container": {
                            "workload_type": conf["container"].get("workload_type", ""),
                            "workload_name": conf["container"].get("workload_name", ""),
                            "container_name": conf["container"].get("container_name", ""),
                            "container_name_exclude": conf["container"].get("container_name_exclude", ""),
                        },
                        "label_selector": {
                            "match_labels": conf["label_selector"].get("match_labels", []),
                            "match_expressions": conf["label_selector"].get("match_expressions", []),
                        },
                        "annotation_selector": {
                            "match_annotations": conf["annotation_selector"].get("match_annotations", []),
                        },
                        "rule_id": rule_id,
                        "parent_container_config_id": 0,
                        "collector_type": ContainerCollectorType.CONTAINER,
                    }
                )

            if conf["enable_stdout"]:
                std_container_config.append(
                    {
                        "namespaces": conf["namespaces"],
                        "namespaces_exclude": conf["namespaces_exclude"],
                        "any_namespace": not conf["namespaces"],
                        "data_encoding": conf["data_encoding"],
                        "params": {
                            "paths": [],
                            "conditions": conf["conditions"]
                            if conf.get("conditions")
                            else {"type": "match", "match_type": "include", "match_content": ""},
                            **conf.get("multiline", {}),
                        },
                        "container": {
                            "workload_type": conf["container"].get("workload_type", ""),
                            "workload_name": conf["container"].get("workload_name", ""),
                            "container_name": conf["container"].get("container_name", ""),
                            "container_name_exclude": conf["container"].get("container_name_exclude", ""),
                        },
                        "label_selector": {
                            "match_labels": conf["label_selector"].get("match_labels", []),
                            "match_expressions": conf["label_selector"].get("match_expressions", []),
                        },
                        "annotation_selector": {
                            "match_annotations": conf["annotation_selector"].get("match_annotations", []),
                        },
                        "rule_id": rule_id,
                        "parent_container_config_id": path_collector.collector_config_id if path_collector else 0,
                        "collector_type": ContainerCollectorType.STDOUT,
                    }
                )
        return path_container_config, std_container_config

    def retry_bcs_config(self, rule_id):
        collectors = CollectorConfig.objects.filter(rule_id=rule_id)
        for collector in collectors:
            self.deal_self_call(
                collector_config_id=collector.collector_config_id,
                collector=collector,
                func=self.retry_container_collector,
            )
        return {"rule_id": rule_id}

    def delete_bcs_config(self, rule_id):
        try:
            bcs_rule = BcsRule.objects.get(id=rule_id)
        except BcsRule.DoesNotExist:
            logger.info(f"[delete_bcs_config] rule_id({rule_id}) does not exist, skipped")
            return {"rule_id": rule_id}

        collectors = CollectorConfig.objects.filter(rule_id=bcs_rule.id)
        for collector in collectors:
            self.deal_self_call(
                collector_config_id=collector.collector_config_id, collector=collector, func=self.destroy
            )
        bcs_rule.delete()
        return {"rule_id": rule_id}

    def start_bcs_config(self, rule_id):
        collectors = CollectorConfig.objects.filter(rule_id=rule_id)
        for collector in collectors:
            self.deal_self_call(collector_config_id=collector.collector_config_id, collector=collector, func=self.start)
        return {"rule_id": rule_id}

    def stop_bcs_config(self, rule_id):
        collectors = CollectorConfig.objects.filter(rule_id=rule_id)
        for collector in collectors:
            self.deal_self_call(collector_config_id=collector.collector_config_id, collector=collector, func=self.stop)
        return {"rule_id": rule_id}

    def delete_collector_bcs_config(self, **kwargs):
        container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=self.data.collector_config_id)
        for config in container_configs:
            self.delete_container_release(config)
        self.destroy()

    @staticmethod
    def get_bcs_config(bk_biz_id: int, bcs_cluster_id: str, storage_cluster_id: int = None):
        bcs_storage_config = BcsStorageClusterConfig.objects.filter(
            bk_biz_id=bk_biz_id, bcs_cluster_id=bcs_cluster_id
        ).first()
        toggle = FeatureToggleObject.toggle(BCS_COLLECTOR)
        conf = toggle.feature_config if toggle else {}
        data_link_id = int(conf.get("data_link_id") or 0)
        data_link_id = get_data_link_id(bk_biz_id=bk_biz_id, data_link_id=data_link_id)
        # 优先使用传的集群ID, 传的集群ID和bcs业务指定存储集群都不存在时, 使用第一个默认集群
        if not storage_cluster_id:
            storage_cluster_id = (
                bcs_storage_config.storage_cluster_id if bcs_storage_config else conf.get("storage_cluster_id")
            )
        if not storage_cluster_id:
            es_clusters = TransferApi.get_cluster_info({"cluster_type": STORAGE_CLUSTER_TYPE, "no_request": True})
            for es in es_clusters:
                if es["cluster_config"]["is_default_cluster"]:
                    storage_cluster_id = es["cluster_config"]["cluster_id"]

        if not storage_cluster_id:
            raise ValueError("default es cluster not exists.")
        return {"data_link_id": data_link_id, "storage_cluster_id": storage_cluster_id}

    def compare_config(self, data, collector_config_id, **kwargs):
        container_configs = ContainerCollectorConfig.objects.filter(collector_config_id=collector_config_id)
        container_configs = list(container_configs)
        config_length = len(data["configs"])
        for x in range(config_length):
            is_all_container = not any(
                [
                    data["configs"][x]["container"]["workload_type"],
                    data["configs"][x]["container"]["workload_name"],
                    data["configs"][x]["container"]["container_name"],
                    data["configs"][x]["container"]["container_name_exclude"],
                    data["configs"][x]["label_selector"]["match_labels"],
                    data["configs"][x]["label_selector"]["match_expressions"],
                    data["configs"][x]["annotation_selector"]["match_annotations"],
                ]
            )
            if x < len(container_configs):
                container_configs[x].namespaces = data["configs"][x]["namespaces"]
                container_configs[x].namespaces_exclude = data["configs"][x]["namespaces_exclude"]
                container_configs[x].any_namespace = not any(
                    [data["configs"][x]["namespaces"], data["configs"][x]["namespaces_exclude"]]
                )
                container_configs[x].data_encoding = data["configs"][x]["data_encoding"]
                container_configs[x].params = (
                    {
                        "paths": data["configs"][x]["paths"],
                        "conditions": {"type": "match", "match_type": "include", "match_content": ""},
                    }
                    if not data["configs"][x]["params"]
                    else data["configs"][x]["params"]
                )
                container_configs[x].workload_type = data["configs"][x]["container"]["workload_type"]
                container_configs[x].workload_name = data["configs"][x]["container"]["workload_name"]
                container_configs[x].container_name = data["configs"][x]["container"]["container_name"]
                container_configs[x].container_name_exclude = data["configs"][x]["container"]["container_name_exclude"]
                container_configs[x].match_labels = data["configs"][x]["label_selector"]["match_labels"]
                container_configs[x].match_expressions = data["configs"][x]["label_selector"]["match_expressions"]
                container_configs[x].match_annotations = data["configs"][x]["annotation_selector"]["match_annotations"]
                container_configs[x].collector_type = data["configs"][x]["collector_type"]
                container_configs[x].all_container = is_all_container
                container_configs[x].raw_config = data["configs"][x].get("raw_config")
                container_configs[x].parent_container_config_id = data["configs"][x].get(
                    "parent_container_config_id", 0
                )
                container_configs[x].rule_id = data["configs"][x].get("rule_id", 0)
                container_configs[x].save()
                container_config = container_configs[x]
            else:
                container_config = ContainerCollectorConfig(
                    collector_config_id=collector_config_id,
                    namespaces=data["configs"][x]["namespaces"],
                    namespaces_exclude=data["configs"][x]["namespaces_exclude"],
                    any_namespace=not any([data["configs"][x]["namespaces"], data["configs"][x]["namespaces_exclude"]]),
                    data_encoding=data["configs"][x]["data_encoding"],
                    params={
                        "paths": data["configs"][x]["paths"],
                        "conditions": {"type": "match", "match_type": "include", "match_content": ""},
                    }
                    if not data["configs"][x]["params"]
                    else data["configs"][x]["params"],
                    workload_type=data["configs"][x]["container"]["workload_type"],
                    workload_name=data["configs"][x]["container"]["workload_name"],
                    container_name=data["configs"][x]["container"]["container_name"],
                    container_name_exclude=data["configs"][x]["container"]["container_name_exclude"],
                    match_labels=data["configs"][x]["label_selector"]["match_labels"],
                    match_expressions=data["configs"][x]["label_selector"]["match_expressions"],
                    match_annotations=data["configs"][x]["annotation_selector"]["match_annotations"],
                    collector_type=data["configs"][x]["collector_type"],
                    all_container=is_all_container,
                    raw_config=data["configs"][x].get("raw_config"),
                    parent_container_config_id=data["configs"][x].get("parent_container_config_id", 0),
                    rule_id=data["configs"][x].get("rule_id", 0),
                )
                container_config.save()
                container_configs.append(container_config)
            self.create_container_release(container_config=container_config)
        delete_container_configs = container_configs[config_length::]
        for config in delete_container_configs:
            # 增量比对后，需要真正删除配置
            self.delete_container_release(config, delete_config=True)

    def create_container_release(self, container_config: ContainerCollectorConfig, **kwargs):
        """
        创建容器采集配置
        :param container_config: 容器采集配置实例
        """
        from apps.log_databus.tasks.collector import create_container_release

        if self.data.yaml_config_enabled and container_config.raw_config:
            # 如果开启了yaml模式且有原始配置，则优先使用
            request_params = copy.deepcopy(container_config.raw_config)
            request_params["dataId"] = self.data.bk_data_id
        else:
            deal_collector_scenario_param(container_config.params)
            request_params = self.collector_container_config_to_raw_config(self.data, container_config)

        # 如果是边缘存查配置，还需要追加 output 配置
        data_link_id = CollectorConfig.objects.get(
            collector_config_id=container_config.collector_config_id
        ).data_link_id
        edge_transport_params = CollectorScenario.get_edge_transport_output_params(data_link_id)
        if edge_transport_params:
            ext_options = request_params.get("extOptions") or {}
            ext_options["output.kafka"] = edge_transport_params
            request_params["extOptions"] = ext_options

        name = self.generate_bklog_config_name(container_config.id)

        container_config.status = ContainerCollectStatus.PENDING.value
        container_config.status_detail = _("等待配置下发")
        container_config.save()

        create_container_release.delay(
            bcs_cluster_id=self.data.bcs_cluster_id,
            container_config_id=container_config.id,
            config_name=name,
            config_params=request_params,
        )

    def generate_bklog_config_name(self, container_config_id) -> str:
        return "{}-{}-{}".format(
            self.data.collector_config_name_en.lower(), self.data.bk_biz_id, container_config_id
        ).replace("_", "-")

    def delete_container_release(self, container_config, delete_config=False):
        from apps.log_databus.tasks.collector import delete_container_release

        name = self.generate_bklog_config_name(container_config.id)
        container_config.status = ContainerCollectStatus.PENDING.value
        container_config.save()

        delete_container_release.delay(
            bcs_cluster_id=self.data.bcs_cluster_id,
            container_config_id=container_config.id,
            config_name=name,
            delete_config=delete_config,
        )

    def list_bcs_clusters(self, bk_biz_id):
        if not bk_biz_id:
            return []
        bcs_clusters = BcsHandler().list_bcs_cluster(bk_biz_id=bk_biz_id)
        for cluster in bcs_clusters:
            cluster["name"] = cluster["cluster_name"]
            cluster["id"] = cluster["cluster_id"]
        return bcs_clusters

    def list_workload_type(self):
        toggle = FeatureToggleObject.toggle(BCS_DEPLOYMENT_TYPE)
        return (
            toggle.feature_config
            if toggle
            else [WorkLoadType.DEPLOYMENT, WorkLoadType.JOB, WorkLoadType.DAEMON_SET, WorkLoadType.STATEFUL_SET]
        )

    def get_cluster_info(self, bk_biz_id, bcs_cluster_id):
        bcs_clusters = BcsHandler().list_bcs_cluster(bk_biz_id=bk_biz_id)
        cluster_info = None
        for c in bcs_clusters:
            if c["cluster_id"] == bcs_cluster_id:
                cluster_info = c
                break

        if cluster_info is None:
            raise BcsClusterIdNotValidException()
        return cluster_info

    @staticmethod
    def _get_shared_cluster_namespace(bk_biz_id: int, bcs_cluster_id: str) -> list[Any]:
        """
        获取共享集群有权限的namespace
        """
        if not bk_biz_id or not bcs_cluster_id:
            return []

        space = Space.objects.get(bk_biz_id=bk_biz_id)

        if space.space_type_id == SpaceTypeEnum.BCS.value:
            project_id_to_ns = BcsHandler().list_bcs_shared_cluster_namespace(
                bcs_cluster_id=bcs_cluster_id, bk_tenant_id=space.bk_tenant_id
            )
            return [{"id": n, "name": n} for n in project_id_to_ns.get(space.space_id, [])]
        elif space.space_type_id == SpaceTypeEnum.BKCC.value:
            # 如果是业务，先获取业务关联了哪些项目，再将每个项目有权限的ns过滤出来
            bcs_projects = BcsApi.list_project({"businessID": bk_biz_id})
            project_ids = {p["projectID"] for p in bcs_projects}
            project_id_to_ns = BcsHandler().list_bcs_shared_cluster_namespace(
                bcs_cluster_id=bcs_cluster_id, bk_tenant_id=space.bk_tenant_id
            )
            namespaces = set()
            for project_id, ns_list in project_id_to_ns.items():
                if project_id not in project_ids:
                    continue
                for ns in ns_list:
                    namespaces.add(ns)
            return [{"id": n, "name": n} for n in namespaces]
        elif space.space_type_id == SpaceTypeEnum.BKCI.value and space.space_code:
            project_id_to_ns = BcsHandler().list_bcs_shared_cluster_namespace(
                bcs_cluster_id=bcs_cluster_id, bk_tenant_id=space.bk_tenant_id
            )
            return [{"id": n, "name": n} for n in project_id_to_ns.get(space.space_code, [])]
        else:
            return []

    def list_namespace(self, bk_biz_id, bcs_cluster_id):
        cluster_info = self.get_cluster_info(bk_biz_id, bcs_cluster_id)
        if cluster_info["is_shared"]:
            return self._get_shared_cluster_namespace(bk_biz_id, bcs_cluster_id)

        api_instance = Bcs(cluster_id=bcs_cluster_id).api_instance_core_v1
        try:
            namespaces = api_instance.list_namespace().to_dict()
        except Exception as e:  # pylint:disable=broad-except
            logger.error(f"call list_namespace{e}")
            raise BCSApiException(BCSApiException.MESSAGE.format(error=e))
        if not namespaces.get("items"):
            return []

        return [
            {"id": namespace["metadata"]["name"], "name": namespace["metadata"]["name"]}
            for namespace in namespaces["items"]
        ]

    def list_topo(self, topo_type, bk_biz_id, bcs_cluster_id, namespace):
        namespace_list = [ns for ns in namespace.split(",") if ns]

        collector_type = (
            ContainerCollectorType.NODE if topo_type == TopoType.NODE.value else ContainerCollectorType.CONTAINER
        )
        self.check_cluster_config(bk_biz_id, collector_type, bcs_cluster_id, namespace_list)

        api_instance = Bcs(cluster_id=bcs_cluster_id).api_instance_core_v1
        result = {"id": bcs_cluster_id, "name": bcs_cluster_id, "type": "cluster"}
        if topo_type == TopoType.NODE.value:
            node_result = []
            nodes = api_instance.list_node().to_dict()
            items = nodes.get("items", [])
            for node in items:
                node_result.append({"id": node["metadata"]["name"], "name": node["metadata"]["name"], "type": "node"})
            result["children"] = node_result
            return result
        if topo_type == TopoType.POD.value:
            result["children"] = []
            if namespace_list:
                for namespace_item in namespace_list:
                    namespace_result = {"id": namespace_item, "name": namespace_item, "type": "namespace"}
                    pods = api_instance.list_namespaced_pod(namespace=namespace_item).to_dict()
                    pod_result = []
                    items = pods.get("items", [])
                    for pod in items:
                        pod_result.append(
                            {"id": pod["metadata"]["name"], "name": pod["metadata"]["name"], "type": "pod"}
                        )
                    namespace_result["children"] = pod_result
                    result["children"].append(namespace_result)
                return result
            pods = api_instance.list_pod_for_all_namespaces().to_dict()
            namespaced_dict = defaultdict(list)
            items = pods.get("items", [])
            for pod in items:
                namespaced_dict[pod["metadata"]["namespace"]].append(
                    {"id": pod["metadata"]["name"], "name": pod["metadata"]["name"], "type": "pod"}
                )
            for namespace, pod in namespaced_dict.items():
                result["children"].append({"id": namespace, "name": namespace, "type": "namespace", "children": pod})
            return result

    def get_labels(self, topo_type, bcs_cluster_id, namespace, name):
        api_instance = Bcs(cluster_id=bcs_cluster_id).api_instance_core_v1
        if topo_type == TopoType.NODE.value:
            nodes = api_instance.list_node(field_selector=f"metadata.name={name}").to_dict()
            return self.generate_label(nodes)
        if topo_type == TopoType.POD.value:
            if not namespace:
                raise MissedNamespaceException()
            pods = api_instance.list_namespaced_pod(
                field_selector=f"metadata.name={name}", namespace=namespace
            ).to_dict()
            return self.generate_label(pods)

    @classmethod
    def generate_label(cls, obj_dict):
        if not obj_dict or not obj_dict["items"]:
            return []
        obj_item, *_ = obj_dict["items"]
        if not obj_item["metadata"]["labels"]:
            return []
        return [
            {"key": label_key, "value": label_valus}
            for label_key, label_valus in obj_item["metadata"]["labels"].items()
        ]

    def filter_pods(
        self,
        pods,
        namespaces=None,
        namespaces_exclude=None,
        workload_type="",
        workload_name="",
        container_name="",
        container_name_exclude="",
        is_shared_cluster=False,
        shared_cluster_namespace=None,
    ):
        namespaces_exclude = namespaces_exclude or []
        container_names = container_name.split(",") if container_name else []
        container_names_exclude = container_name_exclude.split(",") if container_name_exclude else []
        pattern = re.compile(workload_name)
        filtered_pods = []
        shared_cluster_namespace = shared_cluster_namespace or []
        for pod in pods.items:
            # 命名空间匹配
            if namespaces and pod.metadata.namespace not in namespaces:
                continue

            if namespaces_exclude and pod.metadata.namespace in namespaces_exclude:
                continue

            # 共享集群命名空间匹配
            if is_shared_cluster and pod.metadata.namespace not in shared_cluster_namespace:
                continue

            # 工作负载匹配
            if workload_type and not pod.metadata.owner_references:
                continue

            if pod.metadata.owner_references:
                pod_workload_type = pod.metadata.owner_references[0].kind
                pod_workload_name = pod.metadata.owner_references[0].name

                if pod_workload_type == "ReplicaSet":
                    # ReplicaSet 需要做特殊处理
                    pod_workload_name = pod_workload_name.rsplit("-", 1)[0]
                    pod_workload_type = "Deployment"

                if workload_type and workload_type != pod_workload_type:
                    continue

                if workload_name and not pattern.match(pod_workload_name):
                    continue

            # 容器名匹配
            if container_names:
                for container in pod.spec.containers:
                    if container.name in container_names:
                        break
                else:
                    continue

            if container_names_exclude:
                is_break = True
                for container in pod.spec.containers:
                    if container.name not in container_names_exclude:
                        is_break = False
                        break

                if is_break:
                    break

            filtered_pods.append(pod)

        return [(pod.metadata.namespace, pod.metadata.name) for pod in filtered_pods]

    def get_expr_list(self, match_expressions):
        expr_list = []
        for expression in match_expressions:
            if expression["operator"] == LabelSelectorOperator.IN:
                expr = "{} in {}".format(expression["key"], expression["value"])
            elif expression["operator"] == LabelSelectorOperator.NOT_IN:
                expr = "{} notin {}".format(expression["key"], expression["value"])
            elif expression["operator"] == LabelSelectorOperator.EXISTS:
                expr = "{}".format(expression["key"])
            elif expression["operator"] == LabelSelectorOperator.DOES_NOT_EXIST:
                expr = "!{}".format(expression["key"])
            else:
                expr = "{} = {}".format(expression["key"], expression["value"])
            expr_list.append(expr)
        return expr_list

    @staticmethod
    def filter_pods_by_annotations(pods, match_annotations):
        """
        通过annotation过滤pod信息
        """
        # 用于存储符合条件的 pods
        filtered_pods = []
        # 遍历 pods，检查每个 pod 的 annotations
        for pod in pods.items:
            annotations = pod.metadata.annotations
            if not annotations:
                continue

            is_matched = True
            # 遍历match_annotations条件,如果不满足条件,is_matched设置为False
            for _match in match_annotations:
                key = _match["key"]
                op = _match["operator"]
                value = _match["value"].strip("()").split(",")
                if op == LabelSelectorOperator.IN and not (key in annotations and annotations[key] in value):
                    is_matched = False
                elif op == LabelSelectorOperator.NOT_IN and not (key in annotations and annotations[key] not in value):
                    is_matched = False
                elif op == LabelSelectorOperator.EXISTS and key not in annotations:
                    is_matched = False
                elif op == LabelSelectorOperator.DOES_NOT_EXIST and key in annotations:
                    is_matched = False

            if is_matched:
                # 满足匹配条件时,加入到结果列表中
                filtered_pods.append(pod)

        # 把返回的数据重新构建为V1PodList类型
        return client.models.V1PodList(
            api_version=pods.api_version,
            kind=pods.kind,
            items=filtered_pods,
            metadata=pods.metadata,
        )

    def preview_containers(
        self,
        topo_type,
        bk_biz_id,
        bcs_cluster_id,
        namespaces=None,
        namespaces_exclude=None,
        label_selector=None,
        annotation_selector=None,
        container=None,
    ):
        """
        预览匹配到的 nodes 或 pods
        """
        container = container or {}
        namespaces = namespaces or []
        namespaces_exclude = namespaces_exclude or []
        label_selector = label_selector or {}
        annotation_selector = annotation_selector or {}

        # 将标签匹配条件转换为表达式
        match_expressions = label_selector.get("match_expressions", [])

        # match_labels 本质上是个字典，需要去重
        match_labels = {label["key"]: label["value"] for label in label_selector.get("match_labels", [])}
        match_labels_list = [f"{label[0]} = {label[1]}" for label in match_labels.items()]

        match_labels_list.extend(self.get_expr_list(match_expressions))
        label_expression = ", ".join(match_labels_list)

        # annotation selector expr解析
        match_annotations = annotation_selector.get("match_annotations", [])

        api_instance = Bcs(cluster_id=bcs_cluster_id).api_instance_core_v1
        previews = []

        # Node 预览
        if topo_type == TopoType.NODE.value:
            if label_expression:
                # 如果有多条表达式，需要拆分为多个去请求，以获取每个表达式实际匹配的数量
                nodes = api_instance.list_node(label_selector=label_expression)
            else:
                nodes = api_instance.list_node()
            previews.append(
                {"group": "node", "total": len(nodes.items), "items": [item.metadata.name for item in nodes.items]}
            )
            return previews

        # Pod 预览
        # 当存在标签表达式时，以标签表达式维度展示
        # 当不存在标签表达式时，以namespace维度展示
        if label_expression:
            if not namespaces or len(namespaces) > 1 or namespaces_exclude:
                pods = api_instance.list_pod_for_all_namespaces(label_selector=label_expression)
            else:
                pods = api_instance.list_namespaced_pod(label_selector=label_expression, namespace=namespaces[0])
        else:
            if not namespaces or len(namespaces) > 1 or namespaces_exclude:
                pods = api_instance.list_pod_for_all_namespaces()
            else:
                pods = api_instance.list_namespaced_pod(namespace=namespaces[0])

        if match_annotations:
            # 根据annotation过滤
            pods = self.filter_pods_by_annotations(pods, match_annotations)

        is_shared_cluster = False
        shared_cluster_namespace = list()
        cluster_info = self.get_cluster_info(bk_biz_id, bcs_cluster_id)
        if cluster_info.get("is_shared"):
            is_shared_cluster = True
            namespace_info = self._get_shared_cluster_namespace(bk_biz_id, bcs_cluster_id)
            shared_cluster_namespace = [info["name"] for info in namespace_info]

        pods = self.filter_pods(
            pods,
            namespaces=namespaces,
            namespaces_exclude=namespaces_exclude,
            is_shared_cluster=is_shared_cluster,
            shared_cluster_namespace=shared_cluster_namespace,
            **container,
        )

        # 按 namespace进行分组
        namespace_pods = defaultdict(list)
        for pod in pods:
            namespace = pod[0]
            namespace_pods[namespace].append(pod[1])

        for namespace, ns_pods in namespace_pods.items():
            previews.append({"group": f"namespace = {namespace}", "total": len(ns_pods), "items": ns_pods})

        return previews

    @classmethod
    def generate_objs(cls, objs_dict, namespaces=None):
        result = []
        if not objs_dict.get("items"):
            return result
        for item in objs_dict["items"]:
            if not namespaces or item["metadata"]["namespace"] in namespaces:
                # 有指定命名空间的，按命名空间过滤
                result.append(item["metadata"]["name"])
        return result

    def get_workload(self, workload_type, bcs_cluster_id, namespace):
        bcs = Bcs(cluster_id=bcs_cluster_id)

        namespaces = [ns for ns in namespace.split(",") if ns]

        if len(namespaces) == 1:
            workload_type_handler_dict = {
                WorkLoadType.DEPLOYMENT: bcs.api_instance_apps_v1.list_namespaced_deployment,
                WorkLoadType.STATEFUL_SET: bcs.api_instance_apps_v1.list_namespaced_stateful_set,
                WorkLoadType.JOB: bcs.api_instance_batch_v1.list_namespaced_job,
                WorkLoadType.DAEMON_SET: bcs.api_instance_apps_v1.list_namespaced_daemon_set,
            }
            workload_handler = workload_type_handler_dict.get(workload_type)
            if not workload_handler:
                return []
            return self.generate_objs(workload_handler(namespace=namespace).to_dict())

        workload_type_handler_dict = {
            WorkLoadType.DEPLOYMENT: bcs.api_instance_apps_v1.list_deployment_for_all_namespaces,
            WorkLoadType.STATEFUL_SET: bcs.api_instance_apps_v1.list_stateful_set_for_all_namespaces,
            WorkLoadType.JOB: bcs.api_instance_batch_v1.list_job_for_all_namespaces,
            WorkLoadType.DAEMON_SET: bcs.api_instance_apps_v1.list_daemon_set_for_all_namespaces,
        }
        workload_handler = workload_type_handler_dict.get(workload_type)
        if not workload_handler:
            return []

        return self.generate_objs(workload_handler().to_dict(), namespaces=namespaces)

    def validate_container_config_yaml(self, bk_biz_id, bcs_cluster_id, yaml_config: str):
        """
        解析容器日志yaml配置
        """

        class PatchedFullLoader(yaml.FullLoader):
            """
            yaml里面如果有 = 字符串会导致解析失败：https://github.com/yaml/pyyaml/issues/89
            例如:
              filters:
              - conditions:
                - index: "0"
                  key: Jul
                  op: =      # error!
            需要通过这个 loader 去 patch 掉
            """

            yaml_implicit_resolvers = yaml.FullLoader.yaml_implicit_resolvers.copy()
            yaml_implicit_resolvers.pop("=")

        try:
            # 验证是否为合法的 yaml 格式
            configs = [conf for conf in yaml.load_all(yaml_config, Loader=PatchedFullLoader)]
            # 兼容用户直接把整个yaml粘贴过来的情况，这个时候只取 spec 字段
            configs_to_check = [conf["spec"] if "spec" in conf else conf for conf in configs]
            slz = ContainerCollectorYamlSerializer(data=configs_to_check, many=True)
            slz.is_valid(raise_exception=True)

            if not slz.validated_data:
                raise ValueError(_("配置项不能为空"))
        except ValidationError as err:

            def error_msg(value, results):
                if isinstance(value, list):
                    for v in value:
                        error_msg(v, results)
                    return
                for k, v in list(value.items()):
                    if isinstance(v, dict):
                        error_msg(v, results)
                    elif isinstance(v, list) and isinstance(v[0], ErrorDetail):
                        results.append(f"{k}: {v[0][:-1]}")
                    else:
                        for v_msg in v:
                            error_msg(v_msg, results)

            parse_result = []

            def gen_err_topo_message(detail_item: list | dict | str, result_list: list, prefix: str = ""):
                if isinstance(detail_item, str):
                    result_list.append(f"{prefix}: {detail_item}")

                elif isinstance(detail_item, list) and isinstance(detail_item[0], ErrorDetail):
                    gen_err_topo_message(detail_item=detail_item[0], result_list=result_list, prefix=prefix)

                elif isinstance(detail_item, dict):
                    for k, v in detail_item.items():
                        temp_prefix = ".".join([prefix, str(k)]) if prefix else k
                        gen_err_topo_message(detail_item=v, result_list=result_list, prefix=temp_prefix)

            for item in err.detail:
                gen_err_topo_message(detail_item=item, result_list=parse_result)

            return {
                "origin_text": yaml_config,
                "parse_status": False,
                "parse_result": [
                    {"start_line_number": 0, "end_line_number": 0, "message": error} for error in parse_result
                ],
            }
        except Exception as e:  # pylint: disable=broad-except
            return {
                "origin_text": yaml_config,
                "parse_status": False,
                "parse_result": [
                    {"start_line_number": 0, "end_line_number": 0, "message": _("配置格式不合法: {err}").format(err=e)}
                ],
            }

        add_pod_label = False
        add_pod_annotation = False
        extra_labels = {}
        container_configs = []

        for idx, config in enumerate(slz.validated_data):
            log_config_type = config["logConfigType"]

            # 校验配置
            try:
                namespace_list = config.get("namespaceSelector", {}).get("matchNames", [])
                if namespace_list:
                    self.check_cluster_config(
                        bk_biz_id=bk_biz_id,
                        collector_type=log_config_type,
                        bcs_cluster_id=bcs_cluster_id,
                        namespace_list=namespace_list,
                    )
            except AllNamespaceNotAllowedException:
                return {
                    "origin_text": yaml_config,
                    "parse_status": False,
                    "parse_result": [
                        {
                            "start_line_number": 0,
                            "end_line_number": 0,
                            "message": _(
                                "配置校验失败: namespaceSelector 共享集群下 any 不允许为 true，"
                                "且 matchNames 不允许为空，请检查"
                            ),
                        }
                    ],
                }
            except Exception as e:  # noqa
                return {
                    "origin_text": yaml_config,
                    "parse_status": False,
                    "parse_result": [
                        {
                            "start_line_number": 0,
                            "end_line_number": 0,
                            "message": _("配置校验失败: {err}").format(err=e),
                        }
                    ],
                }

            add_pod_label = config["addPodLabel"]
            add_pod_annotation = config["addPodAnnotation"]
            extra_labels = config.get("extMeta", {})
            conditions = convert_filters_to_collector_condition(config.get("filters", []), config.get("delimiter", ""))

            match_expressions = config.get("labelSelector", {}).get("matchExpressions", [])
            for expr in match_expressions:
                # 转换为字符串
                expr["value"] = ",".join(expr.get("values") or [])

            match_annotations = config.get("annotationSelector", {}).get("matchExpressions", [])
            for expr in match_annotations:
                # 转换为字符串
                expr["value"] = ",".join(expr.get("values") or [])

            container_configs.append(
                {
                    "namespaces": config.get("namespaceSelector", {}).get("matchNames", []),
                    "namespaces_exclude": config.get("namespaceSelector", {}).get("excludeNames", []),
                    "container": {
                        "workload_type": config.get("workloadType", ""),
                        "workload_name": config.get("workloadName", ""),
                        "container_name": ",".join(config["containerNameMatch"])
                        if config.get("containerNameMatch")
                        else "",
                        "container_name_exclude": ",".join(config["containerNameExclude"])
                        if config.get("containerNameExclude")
                        else "",
                    },
                    "label_selector": {
                        "match_labels": [
                            {"key": key, "operator": "=", "value": value}
                            for key, value in config.get("labelSelector", {}).get("matchLabels", {}).items()
                        ],
                        "match_expressions": match_expressions,
                    },
                    "annotation_selector": {
                        "match_annotations": match_annotations,
                    },
                    "params": {
                        "paths": config.get("path", []),
                        "exclude_files": config.get("exclude_files", []),
                        "conditions": conditions,
                        "multiline_pattern": config.get("multiline", {}).get("pattern") or "",
                        "multiline_max_lines": config.get("multiline", {}).get("maxLines") or 10,
                        "multiline_timeout": (config.get("multiline", {}).get("timeout") or "10s").rstrip("s"),
                    },
                    "data_encoding": config["encoding"],
                    "collector_type": log_config_type,
                    "raw_config": slz.initial_data[idx],
                }
            )

        return {
            "origin_text": yaml_config,
            "parse_status": True,
            "parse_result": {
                "environment": Environment.CONTAINER,
                "extra_labels": [{"key": key, "value": value} for key, value in extra_labels.items()],
                "add_pod_label": add_pod_label,
                "add_pod_annotation": add_pod_annotation,
                "configs": container_configs,
            },
        }

    def fast_contain_create(self, params: dict) -> dict:
        # 补充缺少的容器参数
        container_configs = params["configs"]
        for container_config in container_configs:
            if not container_config.get("container"):
                container_config["container"] = {
                    "workload_type": "",
                    "workload_name": "",
                    "container_name": "",
                    "container_name_exclude": "",
                }
            if not container_config.get("data_encoding"):
                container_config["data_encoding"] = "UTF-8"

            if not container_config.get("label_selector"):
                container_config["label_selector"] = {"match_labels": [], "match_expressions": []}
            if not container_config["params"].get("conditions", {}).get("type"):
                container_config["params"]["conditions"] = {"type": "none"}
        # 补充缺少的清洗配置参数
        if not params.get("fields"):
            params["fields"] = []
        # 如果没传入集群ID, 则随机给一个公共集群
        if not params.get("storage_cluster_id"):
            storage_cluster_id = get_random_public_cluster_id(bk_biz_id=params["bk_biz_id"])
            if not storage_cluster_id:
                raise PublicESClusterNotExistException()
            params["storage_cluster_id"] = storage_cluster_id
        # 如果没传入数据链路ID, 则按照优先级选取一个集群ID
        data_link_id = int(params.get("data_link_id") or 0)
        params["data_link_id"] = get_data_link_id(bk_biz_id=params["bk_biz_id"], data_link_id=data_link_id)
        # 创建采集项
        self.create_container_config(params)
        params["table_id"] = params["collector_config_name_en"]
        index_set_id = self.create_or_update_clean_config(False, params).get("index_set_id", 0)
        self.send_create_notify(self.data)
        return {
            "collector_config_id": self.data.collector_config_id,
            "bk_data_id": self.data.bk_data_id,
            "subscription_id": self.data.subscription_id,
            "task_id_list": self.data.task_id_list,
            "index_set_id": index_set_id,
        }

    def fast_create(self, params: dict) -> dict:
        params["params"]["encoding"] = params["data_encoding"]
        # 如果没传入集群ID, 则随机给一个公共集群
        if not params.get("storage_cluster_id"):
            storage_cluster_id = get_random_public_cluster_id(bk_biz_id=params["bk_biz_id"])
            if not storage_cluster_id:
                raise PublicESClusterNotExistException()
            params["storage_cluster_id"] = storage_cluster_id
        # 如果没传入数据链路ID, 则按照优先级选取一个集群ID
        data_link_id = int(params.get("data_link_id") or 0)
        params["data_link_id"] = get_data_link_id(bk_biz_id=params["bk_biz_id"], data_link_id=data_link_id)
        self.only_create_or_update_model(params)
        self.create_or_update_subscription(params)

        params["table_id"] = params["collector_config_name_en"]
        index_set_id = self.create_or_update_clean_config(False, params).get("index_set_id", 0)
        self.send_create_notify(self.data)
        return {
            "collector_config_id": self.data.collector_config_id,
            "bk_data_id": self.data.bk_data_id,
            "subscription_id": self.data.subscription_id,
            "task_id_list": self.data.task_id_list,
            "index_set_id": index_set_id,
        }

    def fast_contain_update(self, params: dict) -> dict:
        if self.data and not self.data.is_active:
            raise CollectorActiveException()
        # 补充缺少的清洗配置参数
        params.setdefault("fields", [])
        # 更新采集项
        self.update_container_config(params)
        params["table_id"] = self.data.collector_config_name_en
        self.create_or_update_clean_config(True, params)
        return {"collector_config_id": self.data.collector_config_id}

    def fast_update(self, params: dict) -> dict:
        if self.data and not self.data.is_active:
            raise CollectorActiveException()
        bkdata_biz_id = self.data.bkdata_biz_id if self.data.bkdata_biz_id else self.data.bk_biz_id
        bk_data_name = build_bk_data_name(
            bk_biz_id=bkdata_biz_id, collector_config_name_en=self.data.collector_config_name_en
        )
        self.cat_illegal_ips(params)

        collector_config_fields = [
            "collector_config_name",
            "description",
            "target_object_type",
            "target_node_type",
            "target_nodes",
            "params",
            "extra_labels",
        ]
        model_fields = {i: params[i] for i in collector_config_fields if params.get(i)}

        with transaction.atomic():
            try:
                _collector_config_name = self.data.collector_config_name
                if self.data.bk_data_id and self.data.bk_data_name != bk_data_name:
                    TransferApi.modify_data_id({"data_id": self.data.bk_data_id, "data_name": bk_data_name})
                    logger.info(
                        "[modify_data_name] bk_data_id=>{}, data_name {}=>{}".format(
                            self.data.bk_data_id, self.data.bk_data_name, bk_data_name
                        )
                    )
                    self.data.bk_data_name = bk_data_name

                for key, value in model_fields.items():
                    setattr(self.data, key, value)
                self.data.save()

                # collector_config_name更改后更新索引集名称
                if _collector_config_name != self.data.collector_config_name and self.data.index_set_id:
                    index_set_name = _("[采集项]") + self.data.collector_config_name
                    LogIndexSet.objects.filter(index_set_id=self.data.index_set_id).update(
                        index_set_name=index_set_name
                    )

                # 更新数据源
                if params.get("is_allow_alone_data_id", True):
                    if self.data.etl_processor == ETLProcessorChoices.BKBASE.value:
                        transfer_data_id = self.update_or_create_data_id(
                            self.data, etl_processor=ETLProcessorChoices.TRANSFER.value
                        )
                        self.data.bk_data_id = self.update_or_create_data_id(self.data, bk_data_id=transfer_data_id)
                    else:
                        self.data.bk_data_id = self.update_or_create_data_id(self.data)
                    self.data.save()

            except Exception as e:
                logger.warning(f"modify collector config name failed, err: {e}")
                raise ModifyCollectorConfigException(ModifyCollectorConfigException.MESSAGE.format(e))

        # add user_operation_record
        operation_record = {
            "username": get_request_username(),
            "biz_id": self.data.bk_biz_id,
            "record_type": UserOperationTypeEnum.COLLECTOR,
            "record_object_id": self.data.collector_config_id,
            "action": UserOperationActionEnum.UPDATE,
            "params": model_to_dict(self.data, exclude=["deleted_at", "created_at", "updated_at"]),
        }
        user_operation_record.delay(operation_record)

        try:
            if params.get("params"):
                params["params"]["encoding"] = params["data_encoding"]
                collector_scenario = CollectorScenario.get_instance(self.data.collector_scenario_id)
                self._update_or_create_subscription(
                    collector_scenario=collector_scenario, params=params["params"], is_create=False
                )
        finally:
            if (
                params.get("is_allow_alone_data_id", True)
                and params.get("etl_processor") != ETLProcessorChoices.BKBASE.value
            ):
                # 创建数据平台data_id
                async_create_bkdata_data_id.delay(self.data.collector_config_id)

        params["table_id"] = self.data.collector_config_name_en
        self.create_or_update_clean_config(True, params)

        return {"collector_config_id": self.data.collector_config_id}

    def create_or_update_subscription(self, params):
        """STEP2: 创建|修改订阅"""
        is_create = True if self.data else False
        try:
            collector_scenario = CollectorScenario.get_instance(self.data.collector_scenario_id)
            self._update_or_create_subscription(
                collector_scenario=collector_scenario, params=params["params"], is_create=is_create
            )
        finally:
            if (
                params.get("is_allow_alone_data_id", True)
                and params.get("etl_processor") != ETLProcessorChoices.BKBASE.value
            ):
                # 创建数据平台data_id
                async_create_bkdata_data_id.delay(self.data.collector_config_id)

    def create_or_update_clean_config(self, is_update, params):
        if is_update:
            table_id = self.data.table_id
            # 更新场景，需要把之前的存储设置拿出来，和更新的配置合并一下
            result_table_info = TransferApi.get_result_table_storage(
                {"result_table_list": table_id, "storage_type": "elasticsearch"}
            )
            result_table = result_table_info.get(table_id, {})
            if not result_table:
                raise ResultTableNotExistException(ResultTableNotExistException.MESSAGE.format(table_id))

            default_etl_params = {
                "es_shards": result_table["storage_config"]["index_settings"]["number_of_shards"],
                "storage_replies": result_table["storage_config"]["index_settings"]["number_of_replicas"],
                "storage_cluster_id": result_table["cluster_config"]["cluster_id"],
                "retention": result_table["storage_config"]["retention"],
                "allocation_min_days": params.get("allocation_min_days", 0),
                "etl_config": self.data.etl_config,
            }
            default_etl_params.update(params)
            params = default_etl_params

        from apps.log_databus.handlers.etl import EtlHandler

        etl_handler = EtlHandler.get_instance(self.data.collector_config_id)
        return etl_handler.update_or_create(**params)

    @classmethod
    def collector_container_config_to_raw_config(
        cls, collector_config: CollectorConfig, container_config: ContainerCollectorConfig
    ) -> dict:
        """
        根据采集配置和容器采集配置实例创建容器采集配置
        @param collector_config: 采集配置
        @param container_config: 容器采集配置实例
        @return:
        """
        raw_config = cls.container_config_to_raw_config(container_config)
        raw_config.update(
            {
                "dataId": collector_config.bk_data_id,
                "extMeta": {label["key"]: label["value"] for label in collector_config.extra_labels},
                "addPodLabel": collector_config.add_pod_label,
                "addPodAnnotation": collector_config.add_pod_annotation,
            }
        )
        return raw_config

    @classmethod
    def container_config_to_raw_config(cls, container_config: ContainerCollectorConfig) -> dict:
        """
        根据容器采集配置实例创建容器采集配置
        @param container_config: 容器采集配置实例
        @return:
        """
        filters, _ = deal_collector_scenario_param(container_config.params)
        raw_config = {
            "path": container_config.params["paths"],
            "exclude_files": container_config.params.get("exclude_files", []),
            "encoding": container_config.data_encoding,
            "logConfigType": container_config.collector_type,
            "allContainer": container_config.all_container,
            "namespaceSelector": {
                "any": container_config.any_namespace,
                "matchNames": container_config.namespaces,
                "excludeNames": container_config.namespaces_exclude,
            },
            "workloadType": container_config.workload_type,
            "workloadName": container_config.workload_name,
            "containerNameMatch": container_config.container_name.split(",") if container_config.container_name else [],
            "containerNameExclude": container_config.container_name_exclude.split(",")
            if container_config.container_name_exclude
            else [],
            "labelSelector": {
                "matchLabels": {label["key"]: label["value"] for label in container_config.match_labels}
                if container_config.match_labels
                else {},
                "matchExpressions": [
                    {
                        "key": expression["key"],
                        "operator": expression["operator"],
                        "values": [v.strip() for v in expression.get("value", "").split(",") if v.strip()],
                    }
                    for expression in container_config.match_expressions
                ]
                if container_config.match_expressions
                else [],
            },
            "annotationSelector": {
                "matchExpressions": [
                    {
                        "key": expression["key"],
                        "operator": expression["operator"],
                        "values": [v.strip() for v in expression.get("value", "").split(",") if v.strip()],
                    }
                    for expression in container_config.match_annotations
                ]
                if container_config.match_annotations
                else [],
            },
            "multiline": {
                "pattern": container_config.params.get("multiline_pattern"),
                "maxLines": container_config.params.get("multiline_max_lines"),
                "timeout": (
                    f"{container_config.params['multiline_timeout']}s"
                    if "multiline_timeout" in container_config.params
                    else None
                ),
            },
            "delimiter": container_config.params.get("conditions", {}).get("separator", ""),
            "filters": filters,
        }

        slz = ContainerCollectorYamlSerializer(data=raw_config)
        slz.is_valid(raise_exception=True)

        # validated_data 含有 OrderedDict ，后面 yaml.safe_dump 不支持 OrderedDict，需要转换为原生 dict
        return json.loads(json.dumps(slz.validated_data))

    @classmethod
    def container_dict_configs_to_yaml(
        cls, container_configs: list[dict], add_pod_label: bool, add_pod_annotation: bool, extra_labels: list
    ) -> str:
        """
        将字典格式的容器采集配置转为yaml
        @param container_configs: 容器采集配置实例
        @param add_pod_label: 上报时是否把标签带上
        @param add_pod_annotation: 上报时是否把注解带上
        @param extra_labels: 额外标签
        @return: 将多个配置转为yaml结果
        """
        result = []

        for container_config in container_configs:
            # 排除多的字段，防止在ContainerCollectorConfig作为参数时出现got an unexpected keyword argument
            # 同时需要将该字段内的参数平铺，用来在生成默认的yaml时减少部分产生null值
            for field in CONTAINER_CONFIGS_TO_YAML_EXCLUDE_FIELDS:
                if field in container_config:
                    exclude_field = container_config.pop(field)
                    container_config.update(exclude_field)

            # 与ContainerCollectorConfig创建时计算属性一致
            computed_fields = {
                "all_container": not any(
                    [
                        container_config["workload_type"],
                        container_config["workload_name"],
                        container_config["container_name"],
                        container_config["container_name_exclude"],
                        container_config["match_labels"],
                        container_config["match_expressions"],
                        container_config.get("match_annotations", []),
                    ]
                ),
                "any_namespace": not any([container_config["namespaces"], container_config["namespaces_exclude"]]),
            }
            container_config.update(computed_fields)

            # 参数构造完成后生成raw_config
            container_raw_config = cls.container_config_to_raw_config(ContainerCollectorConfig(**container_config))
            container_raw_config.update(
                {
                    "extMeta": {label["key"]: label["value"] for label in extra_labels if label},
                    "addPodLabel": add_pod_label,
                    "addPodAnnotation": add_pod_annotation,
                }
            )
            result.append(container_raw_config)

        return yaml.safe_dump_all(result)

    @classmethod
    def send_create_notify(cls, collector_config: CollectorConfig):
        try:
            space = Space.objects.get(bk_biz_id=collector_config.bk_biz_id)
            space_uid = space.space_uid
            space_name = space.space_name
        except Space.DoesNotExist:
            space_uid = collector_config.bk_biz_id
            space_name = collector_config.bk_biz_id
        content = _(
            "有新采集项创建，请关注！采集项ID: {}, 采集项名称: {}, 空间ID: {}, 空间名称: {}, 创建者: {}, 来源: {}"
        ).format(
            collector_config.collector_config_id,
            collector_config.collector_config_name,
            space_uid,
            space_name,
            collector_config.created_by,
            collector_config.bk_app_code,
        )

        NOTIFY_EVENT(content=content, dimensions={"space_uid": space_uid, "msg_type": "create_collector_config"})

    @staticmethod
    def search_object_attribute():
        return_data = defaultdict(list)
        response = CCApi.search_object_attribute({"bk_obj_id": "host"})
        for data in response:
            if data["bk_obj_id"] == "host" and data["bk_property_id"] in CC_HOST_FIELDS:
                host_data = {
                    "field": data["bk_property_id"],
                    "name": data["bk_property_name"],
                    "group_name": data["bk_property_group_name"],
                }
                return_data["host"].append(host_data)
        return_data["host"].extend(
            [
                {"field": "bk_supplier_account", "name": "供应商", "group_name": "基础信息"},
                {"field": "bk_host_id", "name": "主机ID", "group_name": "基础信息"},
                {"field": "bk_biz_id", "name": "业务ID", "group_name": "基础信息"},
            ]
        )
        scope_data = [
            {"field": "bk_module_id", "name": "模块ID", "group_name": "基础信息"},
            {"field": "bk_set_id", "name": "集群ID", "group_name": "基础信息"},
            # {"field": "bk_module_name", "name": "模块名称", "group_name": "基础信息"},
            # {"field": "bk_set_name", "name": "集群名称", "group_name": "基础信息"},
        ]
        return_data["scope"] = scope_data
        return return_data

    def update_alias_settings(self, alias_settings):
        """
        修改别名配置
        """
        from apps.log_databus.tasks.collector import update_alias_settings

        update_alias_settings.delay(self.collector_config_id, alias_settings)
        return


def get_data_link_id(bk_biz_id: int, data_link_id: int = 0) -> int:
    """
    获取随机的链路ID
    优先级如下:
    1. 传入的data_link_id
    2. 业务可见的私有链路ID
    3. 公共链路ID
    4. 透传0到监控使用监控的默认链路
    """
    if data_link_id:
        return data_link_id
    # 业务可见的私有链路ID
    data_link_obj = DataLinkConfig.objects.filter(bk_biz_id=bk_biz_id).order_by("data_link_id").first()
    if data_link_obj:
        return data_link_obj.data_link_id
    # 公共链路ID
    data_link_obj = DataLinkConfig.objects.filter(bk_biz_id=0).order_by("data_link_id").first()
    if data_link_obj:
        return data_link_obj.data_link_id

    return data_link_id


def get_random_public_cluster_id(bk_biz_id: int) -> int:
    from apps.log_databus.handlers.storage import StorageHandler

    # 拥有使用权限的集群列表
    clusters = StorageHandler().get_cluster_groups_filter(bk_biz_id=bk_biz_id)
    for cluster in clusters:
        if cluster.get("storage_cluster_id"):
            return cluster["storage_cluster_id"]

    return 0


def build_bk_table_id(bk_biz_id: int, collector_config_name_en: str) -> str:
    """
    根据bk_biz_id和collector_config_name_en构建table_id
    """
    bk_biz_id = int(bk_biz_id)
    if bk_biz_id >= 0:
        bk_table_id = f"{bk_biz_id}_{settings.TABLE_ID_PREFIX}_{collector_config_name_en}"
    else:
        bk_table_id = (
            f"{settings.TABLE_SPACE_PREFIX}_{-bk_biz_id}_{settings.TABLE_ID_PREFIX}_{collector_config_name_en}"
        )
    return bk_table_id


def build_bk_data_name(bk_biz_id: int, collector_config_name_en: str) -> str:
    """
    根据bk_biz_id和collector_config_name_en构建bk_data_name
    @param bk_biz_id:
    @param collector_config_name_en:
    @return:
    """
    bk_biz_id = int(bk_biz_id)
    if bk_biz_id >= 0:
        bk_data_name = f"{bk_biz_id}_{settings.TABLE_ID_PREFIX}_{collector_config_name_en}"
    else:
        bk_data_name = (
            f"{settings.TABLE_SPACE_PREFIX}_{-bk_biz_id}_{settings.TABLE_ID_PREFIX}_{collector_config_name_en}"
        )
    return bk_data_name


def build_result_table_id(bk_biz_id: int, collector_config_name_en: str) -> str:
    """
    根据bk_biz_id和collector_config_name_en构建result_table_id
    @param bk_biz_id:
    @param collector_config_name_en:
    @return:
    """
    bk_biz_id = int(bk_biz_id)
    if bk_biz_id >= 0:
        result_table_id = f"{bk_biz_id}_{settings.TABLE_ID_PREFIX}.{collector_config_name_en}"
    else:
        result_table_id = (
            f"{settings.TABLE_SPACE_PREFIX}_{-bk_biz_id}_{settings.TABLE_ID_PREFIX}.{collector_config_name_en}"
        )
    return result_table_id


def convert_lower_cluster_id(bcs_cluster_id: str):
    """
    将集群ID转换为小写
    例如: BCS-K8S-12345 -> bcs_k8s_12345
    """
    return bcs_cluster_id.lower().replace("-", "_")
