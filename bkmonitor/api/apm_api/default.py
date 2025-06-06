"""
Tencent is pleased to support the open source community by making 蓝鲸智云 - 监控平台 (BlueKing - Monitor) available.
Copyright (C) 2017-2021 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://opensource.org/licenses/MIT
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
"""

from django.conf import settings

from bkmonitor.utils.cache import CacheType
from core.drf_resource.contrib.nested_api import KernelAPIResource


class ApmAPIGWResource(KernelAPIResource):
    TIMEOUT = 300
    base_url_statement = None
    base_url = settings.NEW_MONITOR_API_BASE_URL or "{}/api/bk-monitor/{}/".format(
        settings.BK_COMPONENT_API_URL, settings.APIGW_STAGE
    )

    # 模块名
    module_name = "apm_api"

    @property
    def label(self):
        return self.__doc__


class CreateApplicationResource(ApmAPIGWResource):
    """
    创建apm 应用
    """

    action = "/app/apm/create_apm_application/"
    method = "POST"


class ListApplicationResource(ApmAPIGWResource):
    """
    创建apm 应用
    """

    action = "/app/apm/list_apm_application/"
    method = "GET"


class ApplyDatasourceResource(ApmAPIGWResource):
    """
    创建或者更新apm数据源
    """

    action = "/app/apm/apply_apm_datasource/"
    method = "POST"


class DetailApplicationResource(ApmAPIGWResource):
    action = "/app/apm/detail_apm_application/"
    method = "GET"


class QueryBkDataTokenInfoResource(ApmAPIGWResource):
    action = "/app/apm/query_bk_data_token/"
    method = "GET"


class StartApplicationResource(ApmAPIGWResource):
    action = "/app/apm/start_apm_application/"
    method = "GET"


class StopApplicationResource(ApmAPIGWResource):
    action = "/app/apm/stop_apm_application/"
    method = "GET"


class ListMetaEsClusterInfoResource(ApmAPIGWResource):
    """
    获取Es集群
    """

    action = "/app/apm/list_apm_es_cluster_info/"
    method = "GET"


class QueryInstanceResource(ApmAPIGWResource):
    """
    查询实例
    """

    action = "/app/apm/query_apm_topo_instance/"
    method = "POST"
    backend_cache_type = CacheType.APM


class QueryTopoNodeResource(ApmAPIGWResource):
    """
    查询topo节点信息
    """

    action = "/app/apm/query_apm_topo_node/"
    method = "GET"
    backend_cache_type = CacheType.APM


class QueryTopoRelationResource(ApmAPIGWResource):
    """
    查询topo关系信息
    """

    action = "/app/apm/query_apm_topo_relation/"
    method = "POST"
    backend_cache_type = CacheType.APM


class QueryRootEndpointResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/query_apm_root_endpoint/"
    method = "GET"
    backend_cache_type = CacheType.APM


class QueryEventResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/query_apm_event/"
    method = "POST"


class QuerySpanResource(ApmAPIGWResource):
    """
    查询接口
    """

    action = "/app/apm/query_apm_span/"
    method = "POST"


class QueryEndpointResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/query_apm_endpoint/"
    method = "POST"


class QueryFieldsResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/query_apm_fields/"
    method = "GET"


class UpdateMetricFieldsResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/update_apm_metric_fields/"
    method = "POST"


class QueryEsResource(ApmAPIGWResource):
    """
    查询应用入口接口
    """

    action = "/app/apm/query_apm_es/"
    method = "POST"


class QueryTraceListResource(ApmAPIGWResource):
    """
    Trace查询
    """

    action = "/app/apm/query_apm_trace_list/"
    method = "POST"


class QuerySpanListResource(ApmAPIGWResource):
    """
    Span查询
    """

    action = "/app/apm/query_apm_span_list/"
    method = "POST"


class QuerySpanStatisticsResource(ApmAPIGWResource):
    """
    接口统计查询
    """

    action = "/app/apm/query_apm_span_statistics/"
    method = "POST"


class QueryServiceStatisticsResource(ApmAPIGWResource):
    """
    服务统计查询
    """

    action = "/app/apm/query_apm_service_statistics/"
    method = "POST"


class QueryTraceOptionValuesResource(ApmAPIGWResource):
    """
    Trace候选值查询
    """

    action = "/app/apm/query_apm_trace_option_values/"
    method = "POST"


class QuerySpanOptionValuesResource(ApmAPIGWResource):
    """
    Span候选值查询
    """

    action = "/app/apm/query_apm_span_option_values/"
    method = "POST"


class QueryTraceDetailResource(ApmAPIGWResource):
    """
    查询Trace详情
    """

    action = "/app/apm/query_apm_trace_detail/"
    method = "POST"


class QuerySpanDetailResource(ApmAPIGWResource):
    """
    查询Span详情
    """

    action = "/app/apm/query_apm_span_detail/"
    method = "POST"
    backend_cache_type = CacheType.APM


class ReleaseAppConfigResource(ApmAPIGWResource):
    """
    释放应用配置
    """

    action = "/app/apm/release_apm_app_config/"
    method = "POST"


class DeleteAppConfigResource(ApmAPIGWResource):
    """
    删除应用配置
    """

    action = "/app/apm/delete_apm_app_config/"
    method = "POST"


class GetApplicationConfigResource(ApmAPIGWResource):
    """
    获取应用配置
    """

    action = "/app/apm/query_apm_application_config/"
    method = "GET"


class QueryTraceByIdsResource(ApmAPIGWResource):
    """
    根据traceId列表获取trace信息
    """

    action = "/app/apm/query_trace_by_ids/"
    method = "POST"


class QueryAppByTraceResource(ApmAPIGWResource):
    """
    根据traceId列表获取App关联
    """

    action = "/app/apm/query_app_by_trace/"
    method = "POST"


class QueryAppByHostInstance(ApmAPIGWResource):
    """
    根据ip列表获取App关联
    """

    action = "/app/apm/query_app_by_host_instance/"
    method = "POST"


class QueryTraceByHostInstance(ApmAPIGWResource):
    """
    根据ip获取trace信息
    """

    action = "/app/apm/query_trace_by_host_instance/"
    method = "POST"


class QueryEsMapping(ApmAPIGWResource):
    """
    获取es mapping信息
    """

    action = "/app/apm/query_apm_es_mapping/"
    method = "POST"


class QueryHostInstance(ApmAPIGWResource):
    """
    查询apm主机实例
    """

    action = "/app/apm/query_host_instance/"
    method = "POST"
    backend_cache_type = CacheType.APM


class QueryRemoteServiceRelation(ApmAPIGWResource):
    """
    查询远程服务接口调用关系
    """

    action = "/app/apm/query_remote_service_relation/"
    method = "POST"


class QueryMetricDimensions(ApmAPIGWResource):
    """
    查询指标维度
    """

    action = "/app/apm/query_metric_dimensions/"
    method = "GET"


class DeleteApplication(ApmAPIGWResource):
    """
    删除APM应用
    """

    action = "/app/apm/delete_apm_application/"
    method = "POST"


class QueryDiscoverRules(ApmAPIGWResource):
    """
    查询拓扑发现规则
    """

    action = "/app/apm/query_discover_rules/"
    method = "POST"


class QueryBuiltinProfileDatasourceResource(ApmAPIGWResource):
    cache_type = CacheType.APM(60 * 60 * 24)
    action = "/app/apm/profiling/builtin_profile_datasource/"
    method = "GET"


class QueryEbpfServiceListResource(ApmAPIGWResource):
    """
    获取 ebpf 相关应用服务列表
    """

    action = "/app/apm/profiling/ebpf_service_list"
    method = "POST"


class QueryEbpfProfileResource(ApmAPIGWResource):
    """
    获取单个 ebpf 服务下的 profiling 性能数据
    """

    action = "/app/apm/profiling/ebpf_profile"
    method = "POST"


class GetBkdataFlowDetail(ApmAPIGWResource):
    """
    获取Bkdata flow详情
    """

    action = "/app/apm/get_bkdata_flow/"
    method = "GET"


class CreateOrUpdateBkdataFlow(ApmAPIGWResource):
    """
    创建/更新计算平台Flow
    """

    action = "/app/apm/create_or_update_bkdata_flow/"
    method = "POST"


class OperateApmDataId(ApmAPIGWResource):
    """
    恢复/暂停APM中某个DataId的链路
    """

    action = "/app/apm/operate_apm_dataid/"
    method = "POST"


class QueryProfileServicesDetail(ApmAPIGWResource):
    """
    查询Profile服务详情
    """

    action = "/app/apm/profiling/services_detail/"
    method = "GET"


class QueryFieldsTopkResource(ApmAPIGWResource):
    action = "/app/apm/query_apm_fields_topk/"
    method = "POST"


class QueryFieldStatisticsInfoResource(ApmAPIGWResource):
    action = "/app/apm/query_apm_field_statistics_info/"
    method = "POST"


class QueryFieldStatisticsGraphResource(ApmAPIGWResource):
    action = "/app/apm/query_apm_field_statistics_graph/"
    method = "POST"
