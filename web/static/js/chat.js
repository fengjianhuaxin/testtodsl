/* ============ 聊天逻辑 ============ */
let currentUser = null;
let depthAnalysis = false;

// ---------- 初始化 ----------
document.addEventListener('DOMContentLoaded', async () => {
    await checkLogin();
    await loadHistory();
    initGraph();
    autoResizeTextarea();
});

async function checkLogin() {
    try {
        const data = await API.me();
        if (data.logged_in) {
            currentUser = data.user;
            document.getElementById('userName').textContent = currentUser.name;
            document.getElementById('userRole').textContent =
                currentUser.is_admin ? '管理员' : (currentUser.can_query ? '查询用户' : '访客');
            document.getElementById('loginOverlay').style.display = 'none';

            if (currentUser.is_admin) {
                document.getElementById('adminBtn').style.display = 'flex';
            }
            if (!currentUser.can_query) {
                document.getElementById('noPermission').style.display = 'block';
                document.getElementById('questionInput').disabled = true;
                document.getElementById('sendBtn').disabled = true;
            }
        } else {
            document.getElementById('loginOverlay').style.display = 'flex';
        }
    } catch (e) {
        document.getElementById('loginOverlay').style.display = 'flex';
    }
}

async function doLogout() {
    try { await API.logout(); } catch (e) { }
    localStorage.clear();
    window.location.href = '/login';
}

// ---------- 历史记录 ----------
async function loadHistory() {
    try {
        const history = await API.chatHistory();
        const list = document.getElementById('historyList');
        if (history.length === 0) {
            list.innerHTML = '<div class="history-empty">暂无历史记录</div>';
            return;
        }

        // 按日期分组
        const grouped = {};
        history.forEach(h => {
            const date = h.timestamp.split(' ')[0];
            const label = isToday(date) ? '今天' : (isYesterday(date) ? '昨天' : date);
            if (!grouped[label]) grouped[label] = [];
            grouped[label].push(h);
        });

        let html = '';
        for (const [label, items] of Object.entries(grouped)) {
            html += `<div class="history-title" style="margin-top:8px">${label}</div>`;
            items.forEach(h => {
                html += `<div class="history-item" onclick="loadChatDetail('${h.id}')">
                    <div class="h-question">${escapeHtml(h.question)}</div>
                    <div class="h-time">${h.timestamp.split(' ')[1]}</div>
                </div>`;
            });
        }
        list.innerHTML = html;
    } catch (e) {
        console.error('加载历史失败:', e);
    }
}

function isToday(dateStr) {
    return dateStr === new Date().toISOString().split('T')[0];
}
function isYesterday(dateStr) {
    const d = new Date(); d.setDate(d.getDate() - 1);
    return dateStr === d.toISOString().split('T')[0];
}

async function loadChatDetail(chatId) {
    try {
        const detail = await API.chatDetail(chatId);
        showWelcome(false);
        document.getElementById('messagesList').innerHTML = '';
        addUserMessage(detail.question);
        addBotMessage(detail);
    } catch (e) {
        showToast('加载失败', 'error');
    }
}

// ---------- 发送问题 ----------
async function sendQuestion() {
    const input = document.getElementById('questionInput');
    const question = input.value.trim();
    if (!question) return;

    if (!currentUser || !currentUser.can_query) {
        showToast('您没有查询权限', 'error');
        return;
    }

    input.value = '';
    input.style.height = 'auto';
    showWelcome(false);
    addUserMessage(question);

    const loadingId = addLoadingMessage();

    try {
        const result = await API.chat(question);
        removeLoadingMessage(loadingId);

        if (result.success) {
            addBotMessage(result);

            // 高亮图谱
            const entities = result.intermediates?.step_01_意图澄清?.clarified_intent?.target_entities || [];
            const conditions = result.intermediates?.step_01_意图澄清?.clarified_intent?.conditions || [];
            if (entities.length > 0) {
                highlightGraphNodes(entities, conditions);
            }
        } else {
            addErrorMessage(result.error || '查询失败');
        }

        loadHistory();
    } catch (e) {
        removeLoadingMessage(loadingId);
        addErrorMessage(e.message || '网络错误');
    }
}

function askQuestion(q) {
    document.getElementById('questionInput').value = q;
    sendQuestion();
}

// ---------- UI 操作 ----------
function showWelcome(show) {
    document.getElementById('welcomeScreen').style.display = show ? 'flex' : 'none';
    document.getElementById('messagesContainer').style.display = show ? 'none' : 'block';
}

function startNewChat() {
    showWelcome(true);
    document.getElementById('messagesList').innerHTML = '';
    resetGraphZoom();
}

function addUserMessage(question) {
    const list = document.getElementById('messagesList');
    const div = document.createElement('div');
    div.className = 'message msg-user';
    div.innerHTML = `<div class="msg-content">${escapeHtml(question)}</div>`;
    list.appendChild(div);
    scrollToBottom();
}

function addBotMessage(result) {
    const list = document.getElementById('messagesList');
    const div = document.createElement('div');
    div.className = 'message msg-bot';

    const msgId = 'msg_' + Date.now();
    let html = `<div class="msg-content">`;

    // 回答内容
    html += `<div class="answer-text">${formatAnswer(result.answer || '')}</div>`;

    // 图表
    if (result.chart_url) {
        html += `<div class="chart-container"><img src="${result.chart_url}" alt="统计图表"></div>`;
    }

    // 计算结果表格
    if (result.compute_result && result.compute_result.length > 0) {
        html += renderResultTable(result.compute_result);
    }

    // 下载按钮
    if (result.run_id) {
        html += `<div class="download-bar">
            <a class="download-btn" href="/api/download/dsl/${result.run_id}" download>📄 下载 DSL</a>
            <a class="download-btn" href="/api/download/result/${result.run_id}" download>📊 下载结果</a>
        </div>`;
    }

    // Think 过程
    if (result.intermediates || result.agent_logs) {
        html += renderThinkSection(msgId, result);
    }

    // 中间产物卡片
    if (result.intermediates) {
        html += renderIntermediateCards(msgId, result.intermediates);
    }

    html += `</div>`;
    div.innerHTML = html;
    list.appendChild(div);
    scrollToBottom();
}

function addLoadingMessage() {
    const list = document.getElementById('messagesList');
    const id = 'loading_' + Date.now();
    const div = document.createElement('div');
    div.id = id;
    div.className = 'message msg-bot';
    div.innerHTML = `<div class="loading-indicator">
        <div class="loading-dots"><span></span><span></span><span></span></div>
        <span>Agent 正在分析中，请稍候...</span>
    </div>`;
    list.appendChild(div);
    scrollToBottom();
    return id;
}

function removeLoadingMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function addErrorMessage(msg) {
    const list = document.getElementById('messagesList');
    const div = document.createElement('div');
    div.className = 'message msg-bot';
    div.innerHTML = `<div class="msg-content" style="border-color:rgba(248,113,113,0.3)">
        <span style="color:var(--danger)">⚠️ ${escapeHtml(msg)}</span>
    </div>`;
    list.appendChild(div);
    scrollToBottom();
}

// ---------- 渲染辅助 ----------
function formatAnswer(text) {
    // 处理 markdown 表格
    if (text.includes('|')) {
        const lines = text.split('\n');
        let inTable = false;
        let html = '';
        let tableLines = [];

        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
                if (!inTable) { inTable = true; tableLines = []; }
                tableLines.push(trimmed);
            } else {
                if (inTable) {
                    html += renderMdTable(tableLines);
                    inTable = false;
                    tableLines = [];
                }
                html += escapeHtml(line) + '<br>';
            }
        }
        if (inTable) html += renderMdTable(tableLines);
        return html;
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
}

function renderMdTable(lines) {
    if (lines.length < 2) return lines.join('<br>');
    const header = lines[0].split('|').filter(c => c.trim());
    const rows = lines.slice(2).map(l => l.split('|').filter(c => c.trim()));

    let html = '<table class="result-table"><thead><tr>';
    header.forEach(h => html += `<th>${h.trim()}</th>`);
    html += '</tr></thead><tbody>';
    rows.forEach(row => {
        html += '<tr>';
        row.forEach(c => html += `<td>${c.trim()}</td>`);
        html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
}

function renderResultTable(data) {
    if (!data.length) return '';
    const keys = Object.keys(data[0]);
    let html = '<table class="result-table"><thead><tr>';
    keys.forEach(k => html += `<th>${k}</th>`);
    html += '</tr></thead><tbody>';
    data.forEach(row => {
        html += '<tr>';
        keys.forEach(k => html += `<td>${row[k] !== null && row[k] !== undefined ? row[k] : ''}</td>`);
        html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
}

function renderThinkSection(msgId, result) {
    const thinkId = `think_${msgId}`;
    let stepHtml = '';

    // 从 agent_logs 提取步骤
    if (result.agent_logs && result.agent_logs.length > 0) {
        const steps = [];
        let currentStep = null;

        result.agent_logs.forEach(log => {
            if (log.includes('步骤') || log.includes('Step') || log.includes('Agent') || log.startsWith('🔧') || log.startsWith('✅') || log.startsWith('🔍') || log.startsWith('📋') || log.startsWith('📊') || log.startsWith('🧮') || log.startsWith('⚡') || log.startsWith('🚀')) {
                if (currentStep) steps.push(currentStep);
                const icon = log.match(/^[^\w\s]/)?.[0] || '🔧';
                currentStep = { title: log.substring(0, 60), content: log, icon };
            } else if (currentStep) {
                currentStep.content += '\n' + log;
            } else {
                steps.push({ title: log.substring(0, 60), content: log, icon: '📝' });
            }
        });
        if (currentStep) steps.push(currentStep);

        steps.forEach((step, i) => {
            stepHtml += `<div class="think-step">
                <div class="think-step-title">${step.icon} ${escapeHtml(step.title)}</div>
                <div class="think-step-content">${escapeHtml(step.content)}</div>
            </div>`;
        });
    }

    return `<div class="think-section">
        <div class="think-toggle" onclick="toggleThink('${thinkId}', this)">🧠 思考过程</div>
        <div class="think-details" id="${thinkId}">${stepHtml}</div>
    </div>`;
}

function renderIntermediateCards(msgId, intermediates) {
    const steps = [
        { key: 'step_01_意图澄清', icon: '🔍', label: '意图澄清' },
        { key: 'step_02_知识验证', icon: '✅', label: '知识验证' },
        { key: 'step_03_调度', icon: '🎯', label: '调度决策' },
        { key: 'step_04_查询规划', icon: '📋', label: '子图规划' },
        { key: 'step_05_条件筛选', icon: '🔧', label: '条件筛选' },
        { key: 'step_06_字段提取', icon: '📄', label: '字段提取' },
        { key: 'step_07_计算方法', icon: '🧮', label: '计算方法' },
        { key: 'step_08_DSL查询', icon: '💻', label: 'DSL 查询' },
        { key: 'step_09_计算执行', icon: '⚡', label: '计算执行' },
        { key: 'step_10_质检验证', icon: '🔎', label: '质检验证' },
    ];

    let cardsHtml = '<div class="intermediate-cards">';
    steps.forEach(s => {
        if (intermediates[s.key]) {
            const detailId = `detail_${msgId}_${s.key}`;
            cardsHtml += `<div class="inter-card" onclick="toggleInterCard('${detailId}', this)">
                <span>${s.icon}</span>${s.label}
            </div>`;
        }
    });
    cardsHtml += '</div>';

    // 添加详情面板
    steps.forEach(s => {
        if (intermediates[s.key]) {
            const detailId = `detail_${msgId}_${s.key}`;
            const content = JSON.stringify(intermediates[s.key], null, 2);
            cardsHtml += `<div class="inter-detail" id="${detailId}"><pre>${escapeHtml(content)}</pre></div>`;
        }
    });

    return cardsHtml;
}

// ---------- 交互 ----------
function toggleThink(id, el) {
    const details = document.getElementById(id);
    details.classList.toggle('open');
    el.classList.toggle('open');
}

function toggleInterCard(id, el) {
    // 关闭同级其他详情
    const parent = el.closest('.msg-content');
    parent.querySelectorAll('.inter-detail.open').forEach(d => { if (d.id !== id) d.classList.remove('open'); });
    parent.querySelectorAll('.inter-card.active').forEach(c => { if (c !== el) c.classList.remove('active'); });

    document.getElementById(id).classList.toggle('open');
    el.classList.toggle('active');
}

function toggleDepthAnalysis() {
    depthAnalysis = !depthAnalysis;
    document.querySelector('.depth-analysis').classList.toggle('active', depthAnalysis);
}

function scrollToBottom() {
    const container = document.getElementById('messagesContainer');
    if (container) container.scrollTop = container.scrollHeight;
}

function autoResizeTextarea() {
    const textarea = document.getElementById('questionInput');
    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 侧边栏折叠
document.getElementById('sidebarToggle')?.addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('collapsed');
});
