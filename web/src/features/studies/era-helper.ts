// Annual correspondences: Palace Museum emperor chronologies and RIS year table.
// https://www.dpm.org.cn/court/lineage/226239.html
// https://www.ris.gov.tw/documents/html/8/1/219.html
const eras = [
  { name: "道光", start: 1821, end: 1850 },
  { name: "咸丰", start: 1851, end: 1861 },
  { name: "同治", start: 1862, end: 1874 },
  { name: "光绪", start: 1875, end: 1908 },
  { name: "宣统", start: 1909, end: 1911 },
];
function chineseYear(value: number) {
  if (value === 1) return "元";
  const digits = "零一二三四五六七八九";
  if (value < 10) return digits[value];
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ""}`;
  return `${digits[Math.floor(value / 10)]}十${value % 10 ? digits[value % 10] : ""}`;
}

/** Year labels only. Calendar conversion and historical dating remain source decisions. */
export function yearReference(year: number) {
  if (!Number.isInteger(year) || year < 1 || year > 9999)
    throw new Error("请填写1–9999之间的整数公元年。");
  const index = (((year - 4) % 60) + 60) % 60;
  const names: string[] = [];
  for (const era of eras) {
    if (year - 1 >= era.start && year - 1 <= era.end)
      names.push(`${era.name}${chineseYear(year - era.start)}年（年初候选）`);
    if (year >= era.start && year <= era.end)
      names.push(`${era.name}${chineseYear(year - era.start + 1)}年`);
  }
  if (year >= 1912) names.push(`民国${year === 1912 ? "元" : year - 1911}年`);
  return {
    ganzhi:
      "甲乙丙丁戊己庚辛壬癸"[index % 10] +
      "子丑寅卯辰巳午未申酉戌亥"[index % 12],
    eras: names,
    caution:
      "按公元年编号查常用纪年；清代年初可能沿用上年年次。精确换算需原文月日及纪年制度，不处理春节或立春分界，也不改写史料日期。",
  };
}
