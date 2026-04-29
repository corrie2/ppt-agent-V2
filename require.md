```md
请基于当前 `ppt-agent` 项目修复 `build_html_deck` 的 HTML slide 注入位置问题。

当前问题：

生成的 HTML 中，Guizang 模板原本有：

```html
<div id="deck">
  <!-- SLIDES_HERE -->
</div>
```

但实际生成的 slides 被追加到了后面的：

```html
<main class="deck">
  <section class="slide">...</section>
</main>
```

这导致模板原生脚本：

```js
const deck = document.getElementById('deck');
const slides = deck.querySelectorAll('.slide');
```

无法找到实际 slides，导航、圆点、翻页逻辑都无法正确接管。

## 本任务目标

修复 `build_html_deck`，确保生成的 slides 被注入模板原生 `#deck` 容器。

## 具体要求

1. 不要再额外创建 `<main class="deck">` 承载 slides。
2. 必须把生成的 `<section class="slide">...</section>` 替换到模板中的：

```html
<!-- SLIDES_HERE -->
```

3. 最终 HTML 结构必须类似：

```html
<div id="deck">
  <section class="slide" data-slide-index="1">...</section>
  <section class="slide" data-slide-index="2">...</section>
  ...
</div>
```

4. `#deck .slide` 数量必须等于 plan.slides 数量。
5. 不允许存在空 `#deck` 加另一个有内容的 `.deck`。
6. 不允许最终 HTML 里残留 `<!-- SLIDES_HERE -->`。
7. 不要改 approve 门禁。
8. 不要破坏现有 PPTX build。

## 测试要求

请补测试，至少覆盖：

1. build_html_deck 将 slides 注入 `#deck`
2. 不生成额外 `<main class="deck">`
3. `#deck .slide` 数量等于 plan.slides 数量
4. `<!-- SLIDES_HERE -->` 被替换
5. 全量测试通过

完成后请说明修改了哪些文件、补了哪些测试、pytest 结果。