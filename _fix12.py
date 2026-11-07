p = "sm_scan/gui/queries_view.py"
s = open(p, encoding="utf-8").read()
NL = chr(10)
bad1 = 'head = f"近 7 天命中,前 {len(payload)} 条:" if payload                     else "近 7 天命中 0 条(短语过窄或窗口无新文)。"'
good1 = ('head = (f"近 7 天命中,前 {len(payload)} 条:" if payload'
         + NL + '         else "近 7 天命中 0 条(短语过窄或窗口无新文)。")')
assert bad1 in s
s = s.replace(bad1, good1)
bad2 = 'body = "' + NL + '".join(f"· {t}" for t in payload)'
good2 = 'body = "' + '\n' + '".join(f"· {t}" for t in payload)'
assert bad2 in s
s = s.replace(bad2, good2)
bad3 = 'QMessageBox.information(self, "测试该组", head + "' + NL + '" + body)'
good3 = 'QMessageBox.information(self, "测试该组", head + "' + '\n' + '" + body)'
assert bad3 in s
s = s.replace(bad3, good3)
# 同类:audit 的 msg 三元
bad4 = 'msg = f"校验完成:{len(mism)} 本刊模式与建议不一致" if mism \' + NL + '                else "校验完成:全部一致"'
good4 = ('msg = (f"校验完成:{len(mism)} 本刊模式与建议不一致" if mism' + NL
         + '                else "校验完成:全部一致")')
assert bad4 in s
s = s.replace(bad4, good4)
open(p, "w", encoding="utf-8").write(s)
import ast
ast.parse(s)
print("syntax fixed")
