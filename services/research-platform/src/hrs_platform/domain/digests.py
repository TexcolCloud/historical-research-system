from . import prompts
from .tokens import estimate_request

DIGEST_INSTRUCTIONS = (
    prompts.COMMON
    + """把提供的完整阅读记录整理为供综合分析使用的紧凑提要。
covered_record_indexes 必须按序包含本次每个 record_index，不遗漏、不重复。提要不替代原文或逐批核查。
source_only_unit_ids 标记仅有原文的回退记录：从 candidate_quotes 原文取证，不恢复已撤下的模型解读，
不把空的解读字段当作正文无内容，不将程序回退写成史料局限；归属、数量和否定只能从原文取得。
保留所有对研究必要的事件、主体、具体数字与时间、形成者归属、否定和限定、附注、反证及替代解释。
facts 按具体信息合并重复叙述，每项 source_unit_ids 必须来自本次记录，不能创造新事实。
quotation_candidates 选择足以支持关键事实和限制的原文，unit_id、quote 与 occurrence 沿用实际记录。
不能把一批中的未出现写成全文不存在。结构和跨批接续、页边界及待查问题保持有据可查。
避免把全部字段再抄一遍；书目信息不展开成大段通用提醒。"""
)


def partition(references, load, maximum):
    groups, current = [], []
    for reference in references:
        if (
            current
            and estimate_request({"records": [load(item) for item in current + [reference]]})["input_tokens"]
            > maximum
        ):
            groups.append(current)
            current = []
        current.append(reference)
    if current:
        groups.append(current)
    return groups
