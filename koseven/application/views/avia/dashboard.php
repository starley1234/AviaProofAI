<?php defined('SYSPATH') OR die('No direct script access.'); ?>
<h2>Рабочее место конструктора</h2>
<p>
    Требований: <b><?= (int) $summary['total'] ?></b> ·
    Последняя синхронизация: <b><?= isset($summary['last_sync']) ? $summary['last_sync']['status'] : 'нет' ?></b> ·
    Запись в TC: <b><?= $summary['write_access']['effective'] ? 'разрешена' : 'ЗАПРЕЩЕНА' ?></b>
</p>
<table>
    <tr><th>ID</th><th>Имя</th><th>Раздел</th><th>Статус</th><th>Причина</th></tr>
    <?php foreach ($requirements as $r): ?>
        <tr>
            <td><a href="/avia/requirement/<?= rawurlencode($r['uid']) ?>"><?= HTML::chars($r['item_id']) ?></a></td>
            <td><?= HTML::chars($r['name']) ?></td>
            <td><?= HTML::chars($r['section_path']) ?></td>
            <td><span class="badge <?= HTML::chars($r['status']) ?>"><?= HTML::chars($r['status_label']) ?></span></td>
            <td><?= HTML::chars(implode('; ', $r['status_reasons'] ?: array())) ?></td>
        </tr>
    <?php endforeach; ?>
</table>
