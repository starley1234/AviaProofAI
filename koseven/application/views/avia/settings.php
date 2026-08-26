<?php defined('SYSPATH') OR die('No direct script access.'); ?>
<h2>Настройки записи в Teamcenter</h2>
<table>
    <tr><td>Сервис в целом разрешает запись</td>
        <td><?= $state['service_wide_allowed'] ? 'ДА' : 'НЕТ' ?></td></tr>
    <tr><td>Пользователь имеет право записи</td>
        <td><?= $state['per_user_allowed'] ? 'ДА' : 'НЕТ' ?></td></tr>
    <tr><td><b>Итог (запись возможна)</b></td>
        <td><b><?= $state['effective'] ? 'ДА' : 'НЕТ' ?></b></td></tr>
</table>
<form method="post">
    <label>Глобальный выключатель записи в Teamcenter:</label>
    <select name="enabled">
        <option value="1" <?= $state['service_wide_allowed'] ? 'selected' : '' ?>>Включить</option>
        <option value="0" <?= ! $state['service_wide_allowed'] ? 'selected' : '' ?>>Выключить</option>
    </select>
    <button type="submit">Применить</button>
</form>
<p><small>Требуется роль admin (маппинг пользователей в БД сервиса).</small></p>
