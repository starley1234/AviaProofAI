<?php defined('SYSPATH') OR die('No direct script access.'); ?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>AviaProofAI — двойник требований</title>
    <style>
        body { font-family: sans-serif; margin: 0; background: #f5f6f8; }
        header { background: #1c2b3a; color: #fff; padding: 10px 20px; }
        header a { color: #9fc6ff; margin-right: 16px; text-decoration: none; }
        main { padding: 20px; max-width: 1100px; margin: 0 auto; }
        table { border-collapse: collapse; width: 100%; background: #fff; }
        th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 14px; }
        .badge { padding: 2px 8px; border-radius: 10px; color: #fff; font-size: 12px; }
        .ok { background: #2e7d32; } .weak { background: #f9a825; }
        .conflict { background: #c62828; } .needs_edit { background: #1565c0; }
        .cert_risk { background: #6a1b9a; }
        .flash { background: #fff3cd; border: 1px solid #ffe08a; padding: 8px 12px; margin: 10px 0; }
        textarea { width: 100%; min-height: 120px; font-family: monospace; }
        .row { display: flex; gap: 20px; }
        .col { flex: 1; }
    </style>
</head>
<body>
<header>
    <a href="/avia">Дашборд</a>
    <a href="/avia/analyze">Запустить анализ</a>
    <a href="/avia/settings">Настройки записи в TC</a>
</header>
<main>
    <?php if (isset($flash)): ?><div class="flash"><?= HTML::chars($flash) ?></div><?php endif; ?>
    <?= $content ?>
</main>
</body>
</html>
