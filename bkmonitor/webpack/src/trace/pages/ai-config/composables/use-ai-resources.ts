/*
 * Tencent is pleased to support the open source community by making
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) available.
 *
 * Copyright (C) 2017-2025 Tencent.  All rights reserved.
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
import { shallowRef } from 'vue';

import { AiResourceEnum } from '../constants';
import { getAgentsByIds, getKnowledgebasesByIds, getSkillsByIds } from '../services/ai-resources';

import type { AiResourceType, SourceAnalysisRuleDto } from '../typings';
import type { IAgent, IKnowledgebase, ISkill } from '@blueking/ai-ui-sdk/types';

/** 资源详情项（智能体 / Skill / 知识库） */
export type AiResourceItem = IAgent | IKnowledgebase | ISkill;

/**
 * @description 已选 AI 资源详情管理（智能体 / Skill / 知识库）
 * 列表数据即当前规则已绑定资源的详情，与规则中的资源 ID 保持同步：
 * - 打开侧弹窗时根据规则中的资源 ID 批量查询（fetchResources）；
 * - 资源选择弹窗确认后直接写入回传的资源对象（setResource），无需再次查询；
 * - 删除 / 清空时本地同步移除（removeResource / clearResource）。
 */
export const useAiResources = () => {
  /** 已选智能体列表（单选，0 或 1 项） */
  const agents = shallowRef<IAgent[]>([]);
  /** 已选 Skill 列表 */
  const skills = shallowRef<ISkill[]>([]);
  /** 已选知识库列表 */
  const knowledgebases = shallowRef<IKnowledgebase[]>([]);
  /** 加载中 */
  const loading = shallowRef(false);

  /**
   * @description 根据规则中的资源 ID 批量查询资源详情
   * @param {Pick<SourceAnalysisRuleDto, 'agent_id' | 'knowledge_base_ids' | 'skill_ids'>} rule 规则中的资源 ID
   */
  const fetchResources = async (rule: Pick<SourceAnalysisRuleDto, 'agent_id' | 'knowledge_base_ids' | 'skill_ids'>) => {
    loading.value = true;
    try {
      const { agent_id: agentId, knowledge_base_ids: knowledgebaseIds = [], skill_ids: skillIds = [] } = rule;
      const [agentList, skillList, knowledgebaseList] = await Promise.all([
        agentId ? getAgentsByIds([Number(agentId)]).catch(() => []) : Promise.resolve([]),
        skillIds.length ? getSkillsByIds(skillIds.map(Number)).catch(() => []) : Promise.resolve([]),
        knowledgebaseIds.length
          ? getKnowledgebasesByIds(knowledgebaseIds.map(Number)).catch(() => [])
          : Promise.resolve([]),
      ]);
      agents.value = agentList;
      skills.value = skillList;
      knowledgebases.value = knowledgebaseList;
    } finally {
      loading.value = false;
    }
  };

  /**
   * @description 写入指定类型的已选资源详情（弹窗确认后调用，数据来自弹窗回传）
   * @param {AiResourceType} resourceType 资源类型
   * @param {AiResourceItem[]} items 资源详情列表
   */
  const setResource = (resourceType: AiResourceType, items: AiResourceItem[]) => {
    if (resourceType === AiResourceEnum.AGENT) {
      agents.value = items as IAgent[];
    } else if (resourceType === AiResourceEnum.SKILL) {
      skills.value = items as ISkill[];
    } else {
      knowledgebases.value = items as IKnowledgebase[];
    }
  };

  /**
   * @description 移除指定资源（与规则中的资源 ID 删除操作配套）
   * @param {AiResourceType} resourceType 资源类型
   * @param {string} resourceId 资源 id
   */
  const removeResource = (resourceType: AiResourceType, resourceId: string) => {
    if (resourceType === AiResourceEnum.AGENT) {
      agents.value = [];
    } else if (resourceType === AiResourceEnum.SKILL) {
      skills.value = skills.value.filter(item => String(item.id) !== resourceId);
    } else {
      knowledgebases.value = knowledgebases.value.filter(item => String(item.id) !== resourceId);
    }
  };

  /**
   * @description 清空指定类型的已选资源（与规则中的资源 ID 清空操作配套）
   * @param {AiResourceType} resourceType 资源类型
   */
  const clearResource = (resourceType: AiResourceType) => {
    setResource(resourceType, []);
  };

  /**
   * @description 重置全部资源列表（关闭侧弹窗 / 新增态时调用）
   */
  const resetResources = () => {
    agents.value = [];
    skills.value = [];
    knowledgebases.value = [];
  };

  return {
    /** 已选智能体列表 */
    agents,
    /** 已选 Skill 列表 */
    skills,
    /** 已选知识库列表 */
    knowledgebases,
    /** 加载中 */
    loading,
    /** 根据规则中的资源 ID 批量查询资源详情 */
    fetchResources,
    /** 写入指定类型的已选资源详情 */
    setResource,
    /** 移除指定资源 */
    removeResource,
    /** 清空指定类型的已选资源 */
    clearResource,
    /** 重置全部资源列表 */
    resetResources,
  };
};
