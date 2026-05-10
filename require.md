请更新 README，加入 Document-to-Deck MVP 的使用说明。

需要新增内容：
1. 一段简短说明：
   ppt-agent 现在支持从文档证据生成可追溯 PPTX。
2. CLI 示例：
   - ingest markdown
   - ingest pdf with mineru
   - plan with evidence
   - build with evidence
   - qa
   - repair
3. 解释中间文件：
   - evidence.json
   - plan.json
   - qa_report.json
4. 说明 MinerU 是可选外部工具：
   - 未安装时只能使用 markdown/mock parser
   - PDF 解析需要用户自行安装 MinerU
5. 说明当前限制：
   - 不保证完美理解所有 PDF
   - 不恢复原始 LaTeX
   - 不自动验证所有事实
   - source trace 依赖 parser 输出质量

限制：
- 不要夸大能力。
- 不要说已经支持未实现的功能。
- README 示例必须和实际 CLI 一致。