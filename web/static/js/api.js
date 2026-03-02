/* ============ API 调用封装 ============ */
const API = {
    async request(url, options = {}) {
        const defaults = {
            headers: { 'Content-Type': 'application/json' }
        };
        const res = await fetch(url, { ...defaults, ...options });
        const data = await res.json();
        if (!res.ok && data.error) {
            throw new Error(data.error);
        }
        return data;
    },

    // 认证
    login: (username, password) => API.request('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
    logout: () => API.request('/api/logout', { method: 'POST' }),
    me: () => API.request('/api/me'),

    // 问答
    chat: (question, source) => API.request('/api/chat', { method: 'POST', body: JSON.stringify({ question, source }) }),
    chatHistory: () => API.request('/api/chat/history'),
    chatDetail: (id) => API.request(`/api/chat/history/${id}`),

    // 图谱
    graphFull: () => API.request('/api/graph/full'),
    graphHighlight: (entities, conditions) => API.request('/api/graph/highlight', { method: 'POST', body: JSON.stringify({ entities, conditions }) }),

    // 本体
    getOntology: () => API.request('/api/ontology'),
    getEntities: () => API.request('/api/ontology/entities'),
    addEntity: (data) => API.request('/api/ontology/entities', { method: 'POST', body: JSON.stringify(data) }),
    updateEntity: (name, data) => API.request(`/api/ontology/entities/${name}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteEntity: (name) => API.request(`/api/ontology/entities/${name}`, { method: 'DELETE' }),
    deleteProperty: (entity, prop) => API.request(`/api/ontology/entities/${entity}/properties/${prop}`, { method: 'DELETE' }),
    getRelations: () => API.request('/api/ontology/relations'),
    addRelation: (data) => API.request('/api/ontology/relations', { method: 'POST', body: JSON.stringify(data) }),
    updateRelation: (index, data) => API.request(`/api/ontology/relations/${index}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteRelation: (index) => API.request(`/api/ontology/relations/${index}`, { method: 'DELETE' }),
    importOntologyFromExcel: (data) => API.request('/api/ontology/import-xlsx', { method: 'POST', body: JSON.stringify(data || {}) }),

    // 数据表
    getSources: () => API.request('/api/tables/sources'),
    getTables: (sourceId) => API.request(`/api/tables/${sourceId}`),

    // 映射
    getMapping: (sourceId) => API.request(`/api/mapping/${sourceId}`),
    updateEntityMapping: (sourceId, entity, data) => API.request(`/api/mapping/${sourceId}/entity/${entity}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteEntityMapping: (sourceId, entity) => API.request(`/api/mapping/${sourceId}/entity/${entity}`, { method: 'DELETE' }),
    llmAnalyze: (data) => API.request('/api/mapping/llm-analyze', { method: 'POST', body: JSON.stringify(data) }),

    // 数据
    getData: (sourceId, entity, page, pageSize, search) => API.request(`/api/data/${sourceId}/${entity}?page=${page}&page_size=${pageSize}&search=${search || ''}`),
    addData: (sourceId, entity, data) => API.request(`/api/data/${sourceId}/${entity}`, { method: 'POST', body: JSON.stringify(data) }),
    updateData: (sourceId, entity, idx, data) => API.request(`/api/data/${sourceId}/${entity}/${idx}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteData: (sourceId, entity, idx) => API.request(`/api/data/${sourceId}/${entity}/${idx}`, { method: 'DELETE' }),

    // DSL 日志
    getDSLLogs: (search) => API.request(`/api/dsl-logs?search=${search || ''}`),
    getDSLDetail: (id) => API.request(`/api/dsl-logs/${id}`),

    // MCP
    getMCPs: () => API.request('/api/mcp'),
    addMCP: (data) => API.request('/api/mcp', { method: 'POST', body: JSON.stringify(data) }),
    updateMCP: (id, data) => API.request(`/api/mcp/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteMCP: (id) => API.request(`/api/mcp/${id}`, { method: 'DELETE' }),
    testMCP: (data) => API.request('/api/mcp/test', { method: 'POST', body: JSON.stringify(data) }),

    // DB config
    getDBConfig: () => API.request('/api/db-config'),
    saveDBConfig: (data) => API.request('/api/db-config', { method: 'POST', body: JSON.stringify(data) }),
    testDBConfig: (data) => API.request('/api/db-config/test', { method: 'POST', body: JSON.stringify(data) }),

    // 领域知识
    getKnowledge: () => API.request('/api/knowledge'),
    addKnowledge: (data) => API.request('/api/knowledge', { method: 'POST', body: JSON.stringify(data) }),
    updateKnowledge: (id, data) => API.request(`/api/knowledge/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteKnowledge: (id) => API.request(`/api/knowledge/${id}`, { method: 'DELETE' }),
    getRewriteRules: () => API.request('/api/knowledge/rewrite-rules'),
    addRewriteRule: (data) => API.request('/api/knowledge/rewrite-rules', { method: 'POST', body: JSON.stringify(data) }),
    updateRewriteRule: (id, data) => API.request(`/api/knowledge/rewrite-rules/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteRewriteRule: (id) => API.request(`/api/knowledge/rewrite-rules/${id}`, { method: 'DELETE' }),
    getSqlRules: () => API.request('/api/knowledge/sql-rules'),
    addSqlRule: (data) => API.request('/api/knowledge/sql-rules', { method: 'POST', body: JSON.stringify(data) }),
    updateSqlRule: (id, data) => API.request(`/api/knowledge/sql-rules/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteSqlRule: (id) => API.request(`/api/knowledge/sql-rules/${id}`, { method: 'DELETE' }),

    // 提示词配置
    getPrompts: () => API.request('/api/prompts'),
    updatePrompt: (key, data) => API.request(`/api/prompts/${key}`, { method: 'PUT', body: JSON.stringify(data) }),
    resetPrompt: (key) => API.request(`/api/prompts/${key}/reset`, { method: 'POST' }),
    getPromptLLMConfig: () => API.request('/api/prompts/llm-config'),
    savePromptLLMConfig: (data) => API.request('/api/prompts/llm-config', { method: 'PUT', body: JSON.stringify(data) }),
    resetPromptLLMConfig: () => API.request('/api/prompts/llm-config/reset', { method: 'POST' }),

    // 用户
    getUsers: () => API.request('/api/users'),
    addUser: (data) => API.request('/api/users', { method: 'POST', body: JSON.stringify(data) }),
    updateUser: (username, data) => API.request(`/api/users/${username}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteUser: (username) => API.request(`/api/users/${username}`, { method: 'DELETE' }),
};

function showToast(msg, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}
