/*
 * Tencent is pleased to support the open source community by making
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) available.
 *
 * Copyright (C) 2021 THL A29 Limited, a Tencent company.  All rights reserved.
 *
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) is licensed under the MIT License.
 *
 * License for 蓝鲸智云PaaS平台 (BlueKing PaaS):
 *
 * ---------------------------------------------------
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
 * documentation files (the "Software"), to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
 * to permit persons to whom the Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or substantial portions of
 * the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
 * THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 */
import Vue from 'vue';
import { Component, InjectReactive } from 'vue-property-decorator';
import { Component as tsc } from 'vue-tsx-support';

import {
  MonitorRetrieve as Log,
  initMonitorState,
  initGlobalComponents,
  logStore,
  i18n,
} from '@blueking/monitor-retrieve/main';
import { serviceRelationList, serviceLogInfo } from 'monitor-api/modules/apm_log';

import type { IViewOptions } from '../../typings';
import type { TimeRangeType } from 'monitor-pc/components/time-range/time-range';

import '@blueking/monitor-retrieve/css/mainb33d54b.css';
import './monitor-retrieve.scss';

export const APM_LOG_ROUTER_QUERY_KEYS = ['search_mode', 'addition', 'keyword'];
@Component
export default class MonitorRetrieve extends tsc<void> {
  @InjectReactive('timeRange') readonly timeRange!: TimeRangeType;
  @InjectReactive('timezone') readonly timezone: string;
  // 是否立即刷新
  @InjectReactive('refleshImmediate') readonly refleshImmediate: string;
  // 视图变量
  @InjectReactive('viewOptions') readonly viewOptions!: IViewOptions;
  // 当前使用的业务id
  @InjectReactive('bkBizId') readonly bkBizId: number | string;

  isInit = false;
  empty = true;
  loading = true;
  async created() {
    this.init();
  }
  beforeDestroy() {
    logStore.commit('resetState');
    window.mainComponent.$destroy();
    window.mainComponent = null;
  }

  async init() {
    this.loading = true;
    const data = await this.getServiceLogInfo();
    this.loading = false;
    if (data) {
      this.empty = false;
      const spaceUid =
        window.space_list.find(item => +item.bk_biz_id === +window.bk_biz_id)?.space_uid || window.bk_biz_id;
      window.space_uid = `${spaceUid}`;
      initMonitorState({
        bkBizId: window.bk_biz_id,
        spaceUid,
      });
      initGlobalComponents();
      window.mainComponent = new Vue({
        store: logStore,
        router: this.$router,
        i18n,
        render: h =>
          h(Log, {
            props: {
              indexSetApi: this.indexSetApi,
              timeRange: this.timeRange,
              timezone: this.timezone,
              refleshImmediate: this.refleshImmediate,
            },
          }),
      });
      await this.$nextTick();
      window.mainComponent.$mount(this.$el.querySelector('#main'));
    } else {
      this.empty = true;
    }
  }

  async indexSetApi() {
    const { app_name, service_name } = this.viewOptions.filters;
    const data = await serviceRelationList({
      app_name,
      service_name,
    }).catch(() => []);
    return data;
  }

  async getServiceLogInfo() {
    const data = await serviceLogInfo({
      app_name: this.viewOptions.filters.app_name,
      service_name: this.viewOptions.filters.service_name,
    })
      .then(data => {
        return !!data;
      })
      .catch(() => {
        return false;
      });
    return data;
  }

  /**
   * @desc 关联日志
   */
  handleRelated() {
    const url = `${window.bk_log_search_url}#/manage/log-collection/collection-item?bizId=${
      this.bkBizId || window.bk_biz_id
    }`;
    window.open(url);
  }

  render() {
    return (
      <div class='monitor-retrieve'>
        {this.empty ? (
          <div class='empty-chart-log'>
            {this.loading ? (
              window.i18n.tc('加载中...')
            ) : (
              <bk-exception type='building'>
                <span>{this.$t('暂无关联日志')}</span>
                <div class='text-wrap'>
                  <span class='text-row'>{this.$t('可前往配置页去配置相关日志')}</span>
                  <bk-button
                    theme='primary'
                    onClick={() => this.handleRelated()}
                  >
                    {this.$t('日志采集')}
                  </bk-button>
                </div>
              </bk-exception>
            )}
          </div>
        ) : (
          <div id='main' />
        )}
      </div>
    );
  }
}