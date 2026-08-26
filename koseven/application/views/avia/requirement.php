<?php defined('SYSPATH') OR die('No direct script access.'); ?>
<h2><?= HTML::chars($req['item_id'] . ' — ' . $req['name']) ?>
    <span class="badge <?= HTML::chars($req['status']) ?>"><?= HTML::chars($req['status_label']) ?></span>
</h2>
<p>Раздел: <?= HTML::chars($req['section_path']) ?> · Ревизия: <?= HTML::chars($req['item_revision_id']) ?></p>

<div class="row">
    <div class="col">
        <h3>Текст из Teamcenter (object_string / IMAN_specification)</h3>
        <pre><?= HTML::chars($req['text']) ?></pre>

        <h3>Связи (TC_Requirement_Trace_Relation)</h3>
        <p>
            Вниз: <?= HTML::chars(implode(', ', Arr::get($req['traceability_links'], 'out', array()))) ?><br>
            Вверх: <?= HTML::chars(implode(', ', Arr::get($req['traceability_links'], 'in', array()))) ?>
        </p>

        <h3>Замечания анализа</h3>
        <ul>
            <?php foreach ($req['analyses'] as $a): ?>
                <li>[<?= HTML::chars($a['kind']) ?>/<?= HTML::chars($a['severity']) ?>] <?= HTML::chars($a['title']) ?></li>
            <?php endforeach; ?>
        </ul>
    </div>
    <div class="col">
        <h3>Новая правка (двойник)</h3>
        <form method="post">
            <label>Режим:</label>
            <select name="source">
                <option value="ai">ИИ предложит формулировку</option>
                <option value="user">Ввести вручную</option>
            </select><br><br>
            <textarea name="text" placeholder="Текст правки (для ручного режима)"></textarea><br>
            <input type="text" name="rationale" placeholder="Обоснование правки"><br><br>
            <button type="submit">Создать правку</button>
        </form>
        <h3>История правок</h3>
        <?php foreach ($req['drafts'] as $d): ?>
            <p>
                v<?= (int) $d['version'] ?> [<?= HTML::chars($d['source']) ?>/<?= HTML::chars($d['status']) ?>]
                — <a href="/avia/draft/<?= (int) $d['id'] ?>">открыть</a>
            </p>
        <?php endforeach; ?>
    </div>
</div>
