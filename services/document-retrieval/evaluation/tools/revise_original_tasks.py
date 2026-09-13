"""Append original-review corrections before calibration; never read query results."""

import copy
import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json, utc_now

ROOT = Path(__file__).resolve().parents[1] / "development"


def revise(name, version, changes):
    source = ROOT / (name + ".json")
    destination = ROOT / (name.rsplit("-v", 1)[0] + "-" + version + ".json")
    assert not destination.exists(), destination
    value = json.loads(source.read_text("utf-8"))
    audit = []
    for task in value["tasks"]:
        old = copy.deepcopy(task)
        action = changes.get(task["id"])
        if action:
            reason, updates, replace = action
            task.update(updates)
            task.pop("state", None)
            if replace:
                task["id"] += "-v2"
                task["supersedes_task_id"] = old["id"]
            audit.append(
                {
                    "original_task": old,
                    "replacement_task_id": task["id"],
                    "classification": "input_gap" if replace else "machine_reference_correction",
                    "reason": reason,
                }
            )
    value.update(
        {
            "dataset_id": "retrieval-representative-20260908-v3",
            "scope": "Original-first corrected tasks; fixed-input eligibility and complete anchor groups must be locked before retrieval.",
            "corrected_at": utc_now(),
            "supersedes": {
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "reason": "Original and raw fixed-source reinspection before any evaluated calibration or holdout retrieval.",
            },
            "corrections": audit,
        }
    )
    atomic_json(destination, value)
    print(destination.name, len(audit), "appended corrections")


def main():
    revise(
        "calibration-original-tasks-v2",
        "v3",
        {
            "cal-l06": (
                "Original page 1 identifies 中共中央长江局; the previous machine reference 武汉中心市委 was absent.",
                {
                    "question": "逐字找到中共中央长江局的记载。",
                    "query": "中共中央长江局",
                    "evidence": ["中共中央长江局"],
                },
                False,
            ),
            "cal-r06": (
                "Original spelling is 武汉外围地区, not 武汉外圈地区.",
                {"evidence": ["1938年10月", "1940年9月", "武汉外围地区"]},
                False,
            ),
        },
    )
    revise(
        "holdout-original-tasks",
        "v2",
        {
            "held-l03": (
                "Original and fixed source both say 郑延卓; previous 郑延年 was a machine-reading error.",
                {
                    "question": "逐字查找1942年联络记载中的郑延卓。",
                    "query": "郑延卓",
                    "evidence": ["郑延卓"],
                },
                False,
            ),
            "held-l05": (
                "Original 二次大會今晨揭幕 is malformed/truncated in the frozen extraction. Preserve the original positive as input_gap and substitute another original-visible, qualified headline before retrieval.",
                {
                    "question": "逐字查找报刊关于参政会受期待的繁体标题。",
                    "query": "舉國屬望的參政會",
                    "evidence": ["舉國屬望的參政會"],
                },
                True,
            ),
            "held-l07": (
                "Original 蔣議長致開會詞 is absent from the frozen text. Replace this input_gap with a qualified traditional headline on the same facsimile page before retrieval.",
                {
                    "question": "用简体查询国民参政会第三次大会，回读报刊繁体原字形。",
                    "query": "国民参政会第三次大会",
                    "evidence": ["國民參政會第三次大會"],
                },
                True,
            ),
            "held-l06": (
                "Actual English narrative obtained from Louis Jones's 1987 participant memoir; original page 1 inspected before extraction candidates.",
                {
                    "families": ["english-jones-dixie"],
                    "pages": [1],
                    "question": "逐字找到回忆录作者自述情报军官身份的英文正文。",
                    "query": "I was an intelligence officer in the 45th.",
                    "evidence": ["I was an intelligence officer in the 45th."],
                },
                False,
            ),
            "held-l12": (
                "Actual English body on original page 1 supports ASCII case comparison, not an English abstract or publication title.",
                {
                    "families": ["english-jones-dixie"],
                    "pages": [1],
                    "question": "用大写查询作者英文正文中描述联络职责的短语，回读原文大小写。",
                    "query": "MY PRIMARY ASSIGNMENT",
                    "evidence": ["My primary assignment"],
                },
                False,
            ),
            "held-r01": (
                "Original office directory row is 广州（韶关）, not 南昌.",
                {
                    "question": "比较广州（韶关）与桂林八路军办事处的设置起止时间和负责人，保留表格各列的含义。",
                    "query": "办事处 广州 韶关 桂林 起止时间 负责人",
                    "evidence": [
                        "广州（韶关）",
                        "桂林",
                        "1938.1",
                        "1940.10",
                        "1938.11",
                        "1941.1",
                        "负责人",
                    ],
                },
                False,
            ),
            "held-r06": (
                "Original reports negotiation with 张治中, not a report to the military commission. 减少政治磨擦 was absent; final condition concerns inability immediately to move the army.",
                {
                    "question": "林彪、周恩来在12月24日与张治中谈判时提出哪些条件？保留日期和对应出处注释。",
                    "evidence": [
                        "12月24日",
                        "在允许合法化条件下",
                        "军队要求编四军十二师",
                        "不能实行移动",
                    ],
                },
                False,
            ),
            "held-r10": (
                "Original heading is 外事工作与军事斗争紧密配合; neither 国际友人 nor 爱国侨胞 appears in this article. Reference the actual external publicity and foreign-journalist account.",
                {
                    "question": "南方局外事工作怎样配合军事斗争并联络国际社会？取得对外宣传和联系国外记者的具体论述。",
                    "query": "南方局 外事工作 军事斗争 宣传 国外记者",
                    "evidence": ["外事工作与军事斗争紧密配合", "国外记者", "国际社会"],
                },
                False,
            ),
            "held-r11": (
                "Original formulation is 新机构、新组织、新人员. 新的权力方式 was an unsupported machine paraphrase presented as quotation.",
                {"evidence": ["传统社会结构", "新机构、新组织、新人员", "农村民众"]},
                False,
            ),
            "held-r14": (
                "Original explicitly diagnoses 不平衡性; preserve that qualifier in the reference.",
                {"evidence": ["深度", "不平衡性", "系统化"]},
                False,
            ),
            "held-r15": (
                "Original uses 借款/放款, while 贷款 remains a legitimate concept in the research question. The reference must use the original word.",
                {"evidence": ["重新分配土地", "借款", "变工队", "斯诺"]},
                False,
            ),
            "held-s01": (
                "Original 外事工作与军事斗争紧密配合 corrects the wrong 政治工作 reference.",
                {
                    "evidence_by_family": {
                        "family-09": ["73.21%", "统计凡例"],
                        "family-15": ["外事工作与军事斗争紧密配合"],
                    }
                },
                False,
            ),
            "held-s02": (
                "Original says 桂与我合作, not 桂李合作.",
                {
                    "evidence_by_family": {
                        "family-09": ["桂林", "负责人"],
                        "family-13": ["桂与我合作", "经济命脉"],
                    }
                },
                False,
            ),
            "held-s03": (
                "Neither 爱国侨胞 nor 国际友人 is present in the one-page article. Compare the original donation organization with external press/public-opinion work; do not invent a diaspora fundraising mechanism in the second source.",
                {
                    "question": "为战时社会动员研究，对照华侨常月捐的组织办法与南方局联系国外记者、争取国际社会支持的工作方式。分别保留两个来源的适用范围。",
                    "query": "常月捐 南方局 国外记者 国际社会 支持",
                    "pages_by_family": {"family-16": [13, 14], "family-15": [1]},
                    "evidence_by_family": {
                        "family-16": ["常月捐", "月捐数目"],
                        "family-15": ["国外记者", "国际社会"],
                    },
                },
                False,
            ),
            "held-s06": (
                "Original phrase is 新机构、新组织、新人员; it must not be replaced with an invented literal quote.",
                {
                    "evidence_by_family": {
                        "family-17": ["传统社会结构", "新机构、新组织、新人员"],
                        "family-13": ["税收", "防区"],
                    }
                },
                False,
            ),
            "held-s08": (
                "Reference original 借款, not absent 贷款; original 常月捐 paragraph and note continue on page 14.",
                {
                    "pages_by_family": {"family-20": [24, 25], "family-16": [13, 14]},
                    "evidence_by_family": {
                        "family-20": ["借款", "变工队"],
                        "family-16": ["常月捐", "月捐数目"],
                    },
                },
                False,
            ),
            "held-s09": (
                "自以为正确 is absent on the original inspected pages. The second author discusses narrow source selection and original-archive value; preserve this distinction from observer bias.",
                {
                    "question": "为外国人材料的史料批判补查外交档案研究：前者怎样评议观察者立场，后者怎样评价原始档案的价值与研究材料范围的局限？分别保留两位作者的观点。",
                    "evidence_by_family": {
                        "family-20": ["主客观条件", "阶级偏见"],
                        "family-12": ["原始档案", "视野不够开阔"],
                    },
                },
                False,
            ),
            "held-s10": (
                "Original qualifier is 不平衡性.",
                {
                    "evidence_by_family": {
                        "family-10": ["单干", "学术平台"],
                        "family-20": ["深度", "不平衡性", "系统化"],
                    }
                },
                False,
            ),
            "held-s11": (
                "Newspaper fine print remains uncertain/generated in frozen input, so whole-page media cannot be released. Preserve the media task as input_gap; replace with qualified headline plus independently dated modern caption, and body evidence from the other source. Two separate held-out photo tasks remain mandatory.",
                {
                    "question": "为国共合作公开政治活动补查报刊材料：取得国民参政会社论标题及现代配图说明标注的日期，再与共同祭黄帝陵的正文时间和参与者记载对读。区分原报标题、现代说明与另一文献正文的出处。",
                    "evidence_by_family": {
                        "primary-political-council": ["我們對於國民參政會的意見", "1938年7月5日"],
                        "family-14": ["1937年4月5日", "林伯渠"],
                    },
                    "tags": ["traditional", "primary"],
                },
                True,
            ),
        },
    )


if __name__ == "__main__":
    main()
