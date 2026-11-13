/* 可复用选择题组件（后续课程直接复用）：
   initQuiz("容器id", [{ q, opts: [...], answer: 正确项下标, why: 解析 }, ...])
   点击选项即判分：正确项标绿，错选标红，解析随之展开。 */
function initQuiz(id, questions) {
  var root = document.getElementById(id);
  if (!root) return;
  questions.forEach(function (item, qi) {
    var box = document.createElement("div");
    box.className = "q";
    var title = document.createElement("p");
    title.className = "qtext";
    title.textContent = (qi + 1) + ". " + item.q;
    box.appendChild(title);
    var why = document.createElement("p");
    why.className = "qwhy";
    why.textContent = "解析：" + item.why;
    item.opts.forEach(function (optText, oi) {
      var btn = document.createElement("button");
      btn.className = "opt";
      btn.type = "button";
      btn.textContent = optText;
      btn.addEventListener("click", function () {
        box.querySelectorAll(".opt").forEach(function (b, bi) {
          b.disabled = true;
          if (bi === item.answer) b.classList.add("correct");
          else if (b === btn) b.classList.add("wrong");
        });
        why.classList.add("show");
      });
      box.appendChild(btn);
    });
    box.appendChild(why);
    root.appendChild(box);
  });
}
