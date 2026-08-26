<?php defined('SYSPATH') OR die('No direct script access.');

/**
 * Controller_Avia — рабочее место конструктора (дашборд, требования, правки).
 *
 * Маршрут:  /avia, /avia/requirement/rev-REQ-1201-A, /avia/draft/12, ...
 * Все действия требуют авторизации Koseven (Auth) — здесь показан минимум.
 */
class Controller_Avia extends Controller_Template
{
    /** @var Avia_Api */
    protected $_api;

    public $template = 'avia/template';

    public function before()
    {
        parent::before();
        // авторизация Koseven (в реальном проекте — свой Auth-драйвер)
        $koseven_user = Auth::instance()->get_user();
        if ( ! $koseven_user) {
            $this->request->redirect('auth/login');
        }

        $cfg = Kohana::$config->load('avia');
        $this->_api = new Avia_Api($cfg['api_url'], $cfg['api_key']);
        $this->_api->set_tc_user($this->_tc_login($koseven_user));
        $this->template->set('api', $this->_api);
    }

    /** Маппинг koseven_login -> tc_login живёт в БД сервиса (таблица users). */
    protected function _tc_login($koseven_user)
    {
        $login = is_object($koseven_user) ? $koseven_user->username : (string) $koseven_user;
        $users = $this->_api->list_users();
        foreach ($users as $u) {
            if ($u['koseven_login'] === $login) {
                return $u['tc_login'];
            }
        }
        return $login; // нет маппинга — сервис откажет в записи, чтение доступно
    }

    /** Дашборд: статусы требований + последняя синхронизация + права записи. */
    public function action_index()
    {
        $summary = $this->_api->dashboard_summary();
        $status = $this->request->query('status');
        $reqs = $this->_api->list_requirements($status);

        $this->template->content = View::factory('avia/dashboard', array(
            'summary' => $summary,
            'requirements' => $reqs,
            'filter_status' => $status,
        ));
    }

    /** Карточка требования: текст из TC, предложение ИИ, история правок. */
    public function action_requirement()
    {
        $uid = $this->request->param('id');
        $req = $this->_api->get_requirement($uid);

        if ($this->request->method() === Request::POST) {
            // «ИИ предложит правку» или «сохранить правку конструктора»
            $source = $this->request->post('source') === 'user' ? 'user' : 'ai';
            $draft = $this->_api->create_draft($uid, $source,
                $this->request->post('text'), $this->request->post('rationale'));
            $this->request->redirect('avia/draft/' . $draft['id']);
        }

        $this->template->content = View::factory('avia/requirement', array(
            'req' => $req,
            'trace' => $this->_api->traceability($uid),
        ));
    }

    /** Редактирование правки-двойника: текст, готовность, отправка в TC. */
    public function action_draft()
    {
        $id = (int) $this->request->param('id');
        $draft = $this->_api->get_draft($id);

        if ($this->request->method() === Request::POST) {
            $action = $this->request->post('action');
            try {
                if ($action === 'save') {
                    $draft = $this->_api->update_draft($id, $this->request->post('text'),
                        $this->request->post('rationale'));
                } elseif ($action === 'submit') {
                    $draft = $this->_api->submit_draft($id);
                } elseif ($action === 'push') {
                    $draft = $this->_api->push_draft($id); // 403, если запись запрещена
                } elseif ($action === 'reject') {
                    $draft = $this->_api->reject_draft($id);
                }
                $flash = 'OK';
            } catch (HTTP_Exception $e) {
                $flash = $e->getMessage();
            }
            $this->template->set('flash', $flash);
        }

        $this->template->content = View::factory('avia/drafts', array(
            'draft' => $draft,
            'req' => $this->_api->get_requirement($draft['requirement_uid']),
        ));
    }

    /** Настройки: включение/выключение записи в Teamcenter (admin). */
    public function action_settings()
    {
        $state = $this->_api->write_access();
        if ($this->request->method() === Request::POST) {
            $state = $this->_api->set_write_access(
                $this->request->post('enabled') === '1');
        }
        $this->template->content = View::factory('avia/settings', array('state' => $state));
    }

    /** Запуск анализа (аудит, конфликты, трассируемость). */
    public function action_analyze()
    {
        $this->_api->run_audit();
        $this->_api->run_conflicts();
        $this->_api->run_traceability_audit();
        $this->request->redirect('avia');
    }
}
