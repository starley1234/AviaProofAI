<?php defined('SYSPATH') OR die('No direct script access.'); ?>
<h2>Правка v<?= (int) $draft['version'] ?> · <?= HTML::chars($req['item_id']) ?> —
    статус: <b><?= HTML::chars($draft['status']) ?></b></h2>

<div class="row">
    <div class="col">
        <h3>Оригинал (из Teamcenter)</h3>
        <pre><?= HTML::chars($draft['original_text']) ?></pre>
        <?php if ($draft['proposed_text']): ?>
            <h3>Предложение ИИ</h3>
            <pre><?= HTML::chars($draft['proposed_text']) ?></pre>
        <?php endif; ?>
    </div>
    <div class="col">
        <h3>Рабочий вариант (правка)</h3>
        <form method="post">
            <textarea name="text"><?= HTML::chars($draft['current_text']) ?></textarea>
            <input type="text" name="rationale" value="<?= HTML::chars($draft['rationale']) ?>" placeholder="Обоснование">
            <br><br>
            <button name="action" value="save">Сохранить</button>
            <button name="action" value="submit">Готово (к отправке в TC)</button>
            <button name="action" value="push">Отправить в Teamcenter</button>
            <button name="action" value="reject">Отклонить</button>
        </form>
        <p><small>Отправка возможна только при статусе «ready» и разрешённой записи в TC
            (глобальный выключатель + право пользователя).</small></p>
    </div>
</div>
