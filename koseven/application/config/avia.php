<?php defined('SYSPATH') OR die('No direct script access.');

/**
 * Конфигурация модуля AviaProofAI.
 * api_key должен совпадать с API_KEYS в .env Python-сервиса.
 */
return array(
    'api_url'   => 'http://127.0.0.1:8080/api/v1',
    'api_key'   => 'dev-key-change-me',
    'llm_model' => 'rule-based', // или имя модели, например gpt-4o-mini
);
