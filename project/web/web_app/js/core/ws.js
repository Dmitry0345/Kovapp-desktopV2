/* Клиент WebSocket: переподключение с ростом паузы, ответ на ping сервера,
 * подписка на комнату задачи (task.subscribe), шина событий. */
window.Kov = window.Kov || {};

Kov.WS = (function () {
  let socket = null, wantOpen = false, backoff = 1000, reconnectTimer = null, pingTimer = null;
  let subscribedTask = null;
  const listeners = [];
  let statusListeners = [];
  const reconnectListeners = [];
  let hadConnection = false;   // первый вход — не переподключение

  function on(fn) { if (!listeners.includes(fn)) listeners.push(fn); return () => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); }; }
  function onStatus(fn) { statusListeners.push(fn); }
  function emitStatus(connected) { statusListeners.forEach(fn => { try { fn(connected); } catch (e) { console.error(e); } }); }

  function send(obj) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(obj)); }

  /** Старый сокет замолкает: без этого он продолжал слать события рядом с новым, и всё приходило дважды */
  function drop(ws) {
    if (!ws) return;
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(); } catch (e) { /* */ }
  }

  function connect(force) {
    if (!wantOpen || !Kov.Api.state.token) return;
    clearTimeout(reconnectTimer);
    if (socket && socket.readyState <= 1 && !force) return;   // уже открыт или открывается
    drop(socket);
    let ws;
    try { ws = socket = new WebSocket(Kov.Api.wsUrl()); } catch (e) { socket = null; schedule(); return; }
    socket.onopen = () => {
      // Вход — первым сообщением. В адресе токен оставлять нельзя: он
      // оседает в журналах сервера, и по нему можно войти чужой сессией.
      send({ type: 'auth', token: Kov.Api.state.token });
      backoff = 1000; emitStatus(true);
      if (subscribedTask) send({ type: 'task.subscribe', task_id: subscribedTask });
      // события за время простоя не приходят: даём разделам перечитать данные
      if (hadConnection) reconnectListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
      hadConnection = true;
      clearInterval(pingTimer);
      pingTimer = setInterval(() => send({ type: 'ping' }), 25000);
    };
    socket.onmessage = e => {
      let data; try { data = JSON.parse(e.data); } catch (err) { return; }
      if (!data || !data.type) return;
      if (data.type === 'ping') { send({ type: 'pong' }); return; }
      if (data.type === 'pong' || data.type === 'connection_status') return;
      listeners.forEach(fn => { try { fn(data); } catch (err) { console.error('WS handler', err); } });
    };
    socket.onclose = () => { if (ws !== socket) return; clearInterval(pingTimer); emitStatus(false); schedule(); };
    socket.onerror = () => { /* onclose придёт следом */ };
  }

  function schedule() {
    if (!wantOpen) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 30000);
  }

  function start() { wantOpen = true; connect(); }
  function stop() { wantOpen = false; clearTimeout(reconnectTimer); clearInterval(pingTimer); drop(socket); socket = null; emitStatus(false); }

  function subscribeTask(taskId) {
    taskId = taskId || null;
    if (subscribedTask && subscribedTask !== taskId) send({ type: 'task.unsubscribe', task_id: subscribedTask });
    subscribedTask = taskId;
    if (subscribedTask) send({ type: 'task.subscribe', task_id: subscribedTask });
  }
  /** Ушли из деталей задачи — комнату надо покинуть, иначе события идут впустую */
  function unsubscribeTask() { subscribeTask(null); }
  function onReconnect(fn) { reconnectListeners.push(fn); }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && wantOpen && (!socket || socket.readyState > 1)) { backoff = 1000; connect(true); }
  });
  // сеть вернулась — прежний сокет мог умереть молча: заменяем его новым, а не открываем второй
  window.addEventListener('online', () => { if (wantOpen) { backoff = 1000; connect(true); } });

  return { start, stop, on, onStatus, onReconnect, send, subscribeTask, unsubscribeTask, get connected() { return !!(socket && socket.readyState === 1); } };
})();
