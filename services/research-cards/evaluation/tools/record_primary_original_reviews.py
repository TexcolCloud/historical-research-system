"""Persist completed active-GPT original-first reading of three distinct historical documents."""

from pathlib import Path

from research_cards.records import atomic_json, now, read_json, sha256

FOLDER = Path(__file__).resolve().parents[2] / "evaluation/development/originals/primary-new-fourth-army"
manifest = read_json(FOLDER / "original-manifest.json")
references = {
    "C06": {"pages": list(range(1, 7)), "title": "闽西南军政委员会关于三个月和谈工作情况的报告",
        "formed": "1937-09-17", "printed_pages": "29–34", "necessary_fact_groups": [
            "原件无标题，现标题为编者所加（首页面下注）；1937-09-17报告由闽西南军政委署名，云逸、南委转中央。现代汇编的页码、注释和身份解释不能冒充当时正文。",
            "4月25日常委扩大会及中央指示之后开展和平运动；三个月动员概括为七方面，以粤军、壮丁队实力派为主要谈判对象，以闽人学士为桥梁。时间跨度按报告措辞保存，不反算成唯一无疑的起止日。",
            "第一项党和红军内部报告讨论、计划、巡视及十余次文件指导，结合地方缴械、停止谈判、卢沟桥等具体事件。",
            "第二项公开信、公函、宣言有针对性地送各级部队、公署、学校、绅商及报馆，署长官姓名、征询答复、普遍翻印；报告称有回复及收获，是本方效果陈述。",
            "第三项对炮楼碉堡驻军/壮丁队喊话，溪南例释放被捕群众、进攻较松；不能将局部例子普遍化。",
            "第四项执行停攻，除必要自卫外，停一般武装行动；筹款行为以请商量抗日名义、保护财产及释放等改变。仍有敌袭、自卫、四人死亡及被抓书记/群众，不能称全程绝无损失或从未用武。",
            "第五项谢育才为总代表、邓子恢等及各地代表同粤军、公署、联保谈判；总层次与局部停止军事行动、移民返家、给养、停止抓人的条件要区分。",
            "温仰春、张思垣先联系联保再军方、公署、绅商；龙岩、永靖、和靖等地方谈判取得停剿、通行、宣传、筹饷和治安合作。身份/地名的编者注释应保留。",
            "张、邓名义致漳厦同乡会公函、争取和平和募给养；已得数千元，与若不受阻可得万元以上的反事实估计严格分开。",
            "第六项把和平联结群众利益：移民返乡、裁撤壮丁常备、土地暂维持、减租税；永和靖剿匪/禁烟赌和姓氏地方纠纷调解。公审处决、释放、保释是报告所记治理手段，不能只留赞词。",
            "第七项政治警觉、严密敌情与准备自卫；7月21日永定被袭、撤退遇埋伏死亡四人，属于其胜利自评的反向限定。",
            "报告结论称争取各界及部分对手支持，但反复提醒部分人怀鬼胎、谣言仍会继续；附注明确只写经验和胜利，个别缺点/错误未列、称正在纠正。这个选择性报喜范围是核心来源局限，不能遗漏。"
        ]},
    "C07": {"pages": [7], "title": "张闻天、毛泽东关于着董必武派人与项英、陈毅联络致林伯渠电",
        "formed": "1937-09-28", "printed_pages": "35", "necessary_fact_groups": [
            "独立短电报，正文称呼林，落款洛、毛，二十八日申；页上标题和括号1937年9月28日提供编排中的人物/日期解释，正文署名与完整名称分层呈现。",
            "核心指令原文为：对项英、陈毅两同志处，先令董老派人联络，告以情况与政策。受命转联络的链条是董老派人至项英/陈毅处，不是林本人或毛本人已完成访问。",
            "这是先行联络与传达情况政策的命令，只能证明指示，未给联络人的姓名、到达时间、政策内容、执行成效或对方回复。",
            "零独立论证条目可以合理，只要概览、文本结构、核心证据、用途和局限完整。现代汇编页不等于1937年电报手稿影像。"
        ]},
    "C08": {"pages": [8, 9], "title": "中共中央书记处关于南方各游击队集中改编方针致张云逸等电",
        "formed": "1937-10-01", "printed_pages": "36–37", "necessary_fact_groups": [
            "1937-10-01中央书记处电，受文云逸、南杰、博古、剑英，并告周、朱、彭、任及伯渠；正文简称及页下注的全名、职务解释须分开。",
            "甲乙丙把南方游击区说成十年流血获得的未来战略支点，并判断国民党借抗日图拔除，全部集中不利。这是发电者政策认识和敌我判断，不是独立调查结论。",
            "原则上不拒绝集中，但须中央派人传达、至少几个月；这是有条件安排，不能简化为一概拒绝集中或立即无条件集中。",
            "六项前提：邻近周围两百里驻军/保安/民团先调动（至少同时且调后不再来）；按附近驻军民团数量保留游击队保护家属；民选制度；土地关系不变；不得派人移入破坏；中央传达与时间为另一个条件。精确两百里与先/至少同时不能丢。",
            "国民党必须先归还何鸣部人枪、查实无误后才谈各区问题，此句从p8接到p9，不能截断。",
            "张鼎丞、何鸣、刘英三部原地不动，因预判日本进攻粤闽浙，保卫各区及附近土地成果，明确这三部不应集中；不同于所有部队永不集中。",
            "解决所有问题后内地若干部队集中，领导指挥和作战不许国民党干涉或插入人员。",
            "叶挺须来延安，完全同意中央政治军事原则后才可指挥闽粤边/闽浙边张鼎丞或刘英部，在此基础扩大部队；先后、条件及括号备选保留。",
            "末段批评项英对统一战线独立性和保存支点认识不明，称南昌做法危险、通知来延安讨论。这是中央的当时批评，不等于已证明其全部行动或后续结果。",
            "电报到p9署中央书记处、十月一日结束，p9刘英任职注仍属于附注；不能把p8和p9当两篇独立文献，不能据标题页宣称持有原电手稿。"
        ]}
}
for sample, details in references.items():
    images = [row for row in manifest["images"] if row["physical_page"] in details["pages"]]
    assert all(sha256(Path(row["path"]).read_bytes()) == row["sha256"] for row in images)
    atomic_json(FOLDER / (sample + "-original-reference.json"), {"at": now(),
        "reviewer": "active_codex_gpt_vision", "original_first": True, "candidate_seen": False,
        "machine_review": True, "human_review": False, "sealed_gold": False,
        "source_sha256": manifest["source_sha256"], "original_manifest_sha256": sha256((FOLDER / "original-manifest.json").read_bytes()),
        "original_images": images, "source_layer": "contemporary-formed document reproduced in a later compiled volume; editorial titles/notes separate",
        "purpose": "calibration original-first reference; never generator input", **details})
    print({"sample": sample, "original_pages": details["pages"], "candidate_seen": False})
