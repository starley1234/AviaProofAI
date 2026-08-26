<?php defined('SYSPATH') OR die('No direct script access.');

/**
 * Avia_Api — cURL-клиент REST API аналитического сервиса AviaProofAI.
 *
 * Каждый метод = один эндпоинт API (см. docs/api.md). Ошибки сервиса
 * пробрасываются как Avia_Api_Exception с HTTP-статусом.
 */
class Avia_Api
{
    /** @var string */
    protected $_base;
    /** @var string */
    protected $_key;
    /** @var string */
    protected $_tc_user = '';

    public function __construct($base, $key)
    {
        $this->_base = rtrim($base, '/');
        $this->_key  = $key;
    }

    /**
     * TC-логин текущего пользователя (из маппинга в БД сервиса).
     * Передаётся в заголовке X-TC-User — по нему сервис проверяет право записи.
     */
    public function set_tc_user($tc_login)
    {
        $this->_tc_user = $tc_login;
        return $this;
    }

    /** HTTP-вызов. $method: GET|POST|PATCH|PUT */
    protected function _call($method, $path, $body = NULL)
    {
        $ch = curl_init($this->_base . $path);
        $headers = array(
            'X-Api-Key: ' . $this->_key,
            'Content-Type: application/json',
        );
        if ($this->_tc_user !== '') {
            $headers[] = 'X-TC-User: ' . $this->_tc_user;
        }
        curl_setopt_array($ch, array(
            CURLOPT_RETURNTRANSFER => TRUE,
            CURLOPT_TIMEOUT        => 60,
            CURLOPT_CUSTOMREQUEST  => $method,
            CURLOPT_HTTPHEADER     => $headers,
        ));
        if ($body !== NULL) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body, JSON_UNESCAPED_UNICODE));
        }
        $raw = curl_exec($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        $err = curl_error($ch);
        curl_close($ch);
        if ($err !== '') {
            throw new HTTP_Exception_500('AviaProofAI недоступен: ' . $err);
        }
        $data = json_decode($raw, TRUE);
        if ($status >= 400) {
            $msg = is_array($data) && isset($data['detail'])
                ? (is_array($data['detail']) ? json_encode($data['detail']) : $data['detail'])
                : 'HTTP ' . $status;
            throw new HTTP_Exception($status, 'AviaProofAI: ' . $msg);
        }
        return $data;
    }

    // ─────────────── дашборд и требования ───────────────
    public function dashboard_summary()
    {
        return $this->_call('GET', '/dashboard/summary');
    }

    public function list_requirements($status = NULL, $search = NULL, $section = NULL)
    {
        $q = http_build_query(array_filter(array(
            'status' => $status, 'search' => $search, 'section' => $section,
        )));
        return $this->_call('GET', '/requirements' . ($q ? '?' . $q : ''));
    }

    public function get_requirement($uid)
    {
        return $this->_call('GET', '/requirements/' . rawurlencode($uid));
    }

    public function traceability($uid)
    {
        return $this->_call('GET', '/requirements/' . rawurlencode($uid) . '/traceability');
    }

    // ─────────────── анализ ───────────────
    public function run_audit()
    {
        return $this->_call('POST', '/analysis/audit');
    }

    public function run_conflicts()
    {
        return $this->_call('POST', '/analysis/conflicts');
    }

    public function run_traceability_audit()
    {
        return $this->_call('POST', '/analysis/traceability');
    }

    public function analysis_results($kind = NULL, $status = NULL)
    {
        $q = http_build_query(array_filter(array('kind' => $kind, 'status' => $status)));
        return $this->_call('GET', '/analysis/results' . ($q ? '?' . $q : ''));
    }

    public function resolve_result($id, $resolution = 'resolved')
    {
        return $this->_call('POST', '/analysis/results/' . (int) $id . '/resolve',
            array('resolution' => $resolution));
    }

    // ─────────────── двойник: правки ───────────────
    /** source: ai — ИИ предложит формулировку; user — текст конструктора. */
    public function create_draft($uid, $source, $text = NULL, $rationale = '')
    {
        return $this->_call('POST', '/requirements/' . rawurlencode($uid) . '/drafts',
            array('source' => $source, 'text' => $text, 'rationale' => $rationale));
    }

    public function update_draft($id, $text, $rationale = NULL)
    {
        return $this->_call('PATCH', '/drafts/' . (int) $id,
            array_filter(array('text' => $text, 'rationale' => $rationale), function ($v) {
                return $v !== NULL;
            }));
    }

    public function get_draft($id)
    {
        return $this->_call('GET', '/drafts/' . (int) $id);
    }

    public function submit_draft($id)
    {
        return $this->_call('POST', '/drafts/' . (int) $id . '/submit');
    }

    public function reject_draft($id)
    {
        return $this->_call('POST', '/drafts/' . (int) $id . '/reject');
    }

    /** Миграция правки в Teamcenter. 403 — запись запрещена настройками. */
    public function push_draft($id)
    {
        return $this->_call('POST', '/drafts/' . (int) $id . '/push');
    }

    public function impact($uid, $new_text)
    {
        return $this->_call('POST', '/requirements/' . rawurlencode($uid) . '/impact',
            array('new_text' => $new_text));
    }

    // ─────────────── настройки и пользователи ───────────────
    public function write_access($user = NULL)
    {
        $q = $user !== NULL ? '?user=' . rawurlencode($user) : '';
        return $this->_call('GET', '/settings/write-access' . $q);
    }

    public function set_write_access($enabled)
    {
        return $this->_call('PUT', '/settings/write-access', array('enabled' => (bool) $enabled));
    }

    public function list_users()
    {
        return $this->_call('GET', '/users');
    }
}
