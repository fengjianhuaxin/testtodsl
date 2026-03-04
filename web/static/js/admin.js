/* ============ 绠＄悊绔€昏緫 ============ */
let currentUser = null;
let selectedEntity = null;
let ontologyGraph = null;

// ---------- 鍒濆鍖?----------
document.addEventListener('DOMContentLoaded', async () => {
    await checkAdminLogin();
    initTabs();
    loadOntologyModule();
});

async function checkAdminLogin() {
    try {
        const data = await API.me();
        if (data.logged_in) {
            currentUser = data.user;
            document.getElementById('adminUserName').textContent = currentUser.name;
            if (!currentUser.is_admin) {
                showToast('闇€瑕佺鐞嗗憳鏉冮檺', 'error');
                setTimeout(() => window.location.href = '/', 1500);
                return;
            }
            document.getElementById('loginOverlay').style.display = 'none';
        } else {
            document.getElementById('loginOverlay').style.display = 'flex';
        }
    } catch (e) {
        document.getElementById('loginOverlay').style.display = 'flex';
    }
}

// ---------- Tab 导航 ----------
function initTabs() {
    document.querySelectorAll('.admin-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.module-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const mod = tab.dataset.module;
            document.getElementById(`mod-${mod}`).classList.add('active');
            // 加载模块数据
            const loaders = {
                ontology: loadOntologyModule,
                tables: loadTablesModule,
                mapping: loadMappingModule,
                data: loadDataModule,
                dsl: loadDSLLogs,
                mcp: loadMCPs,
                db: loadDBConfigModule,
                knowledge: loadKnowledgeModule,
                prompts: loadPromptsModule,
                users: loadUsers
            };
            if (loaders[mod]) loaders[mod]();
        });
    });
}

// ============================================================
// 1. 本体管理
// ============================================================
async function loadOntologyModule() {
    try {
        const onto = await API.getOntology();
        renderEntityList(onto.entities);
        renderOntologyGraph(onto);
        renderRelationList(onto.relations);
    } catch (e) { showToast('加载本体失败', 'error'); }
}

function showImportOntologyModal() {
    showModal('导入本体Excel（重建本体+映射）', `
        <div class="form-row">
            <label>Excel 路径（可留空自动取下载目录最新“数据要素本体*.xlsx”）</label>
            <input class="form-input" id="m-import-excel" placeholder="例如: C:\\Users\\dahe\\AppData\\Roaming\\EpointMsg\\downloadFiles\\数据要素本体1.0(1).xlsx">
        </div>
        <div style="font-size:12px;color:var(--text-muted);line-height:1.7">
            导入规则：按本体优先重建。<br>
            仅对能匹配到数据表/字段的本体建立映射；无法匹配的本体仅保留在本体中。
        </div>
    `, async () => {
        const excelPath = gv('m-import-excel');
        const result = await API.importOntologyFromExcel({ excel_path: excelPath });
        closeModal();
        await loadOntologyModule();
        showToast(
            `导入完成：实体${result.entity_count}，关系${result.relation_count}，映射${result.mapped_entity_count}，未映射${result.unmapped_entity_count}`,
            'success'
        );
    });
}

function renderEntityList(entities) {
    const el = document.getElementById('entityList');
    let html = '';
    for (const [name, entity] of Object.entries(entities)) {
        const propCount = Object.keys(entity.properties || {}).length;
        const entityKey = encodeURIComponent(name);
        html += `<div class="card" style="cursor:pointer;padding:14px" onclick="selectEntityByKey('${entityKey}')"
                      id="entity-card-${name}">
            <div style="font-weight:600;color:var(--text-primary)">${entity.label || name}</div>
            <div style="font-size:12px;color:var(--text-muted)">${name} · ${propCount} 个属性</div>
        </div>`;
    }
    el.innerHTML = html;
}

async function selectEntity(name) {
    selectedEntity = name;
    document.querySelectorAll('[id^=entity-card-]').forEach(c => c.style.borderColor = '');
    const card = document.getElementById(`entity-card-${name}`);
    if (card) card.style.borderColor = 'var(--accent)';

    try {
        const entity = await API.request(`/api/ontology/entities/${name}`);
        renderEntityDetail(name, entity);
    } catch (e) { showToast('加载实体失败', 'error'); }
}

function renderEntityDetail(name, entity) {
    const el = document.getElementById('entityDetail');
    const entityKey = encodeURIComponent(name);
    const entityAliases = stringifyAliasList(entity.aliases);
    let propsHtml = '<div class="table-scroll"><table class="data-table"><thead><tr><th>属性名</th><th>类型</th><th>标签</th><th>别名</th><th>值别名</th><th>描述</th><th></th></tr></thead><tbody>';
    for (const [pname, pdef] of Object.entries(entity.properties || {})) {
        const propKey = encodeURIComponent(pname);
        const tags = [];
        if (pdef.is_key) tags.push('<span class="badge badge-blue">主键</span>');
        if (pdef.is_fk) tags.push(`<span class="badge badge-yellow">外键→${pdef.ref_entity}</span>`);
        const aliases = stringifyAliasList(pdef.aliases) || '-';
        const valueAliases = pdef.value_aliases && Object.keys(pdef.value_aliases).length > 0
            ? escHtml(JSON.stringify(pdef.value_aliases))
            : '-';
        propsHtml += `<tr>
            <td><strong>${pname}</strong></td>
            <td>${pdef.type || 'string'}</td>
            <td>${pdef.label || ''} ${tags.join(' ')}</td>
            <td>${escHtml(aliases)}</td>
            <td title="${valueAliases}">${valueAliases}</td>
            <td>${pdef.description || ''}</td>
            <td>
                <button class="btn btn-secondary btn-sm" onclick="showEditPropertyModalByKey('${entityKey}','${propKey}')">编辑</button>
                <button class="btn btn-danger btn-sm" onclick="deletePropertyByKey('${entityKey}','${propKey}')">删除</button>
            </td>
        </tr>`;
    }
    propsHtml += '</tbody></table></div>';

    el.innerHTML = `
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div>
                    <div class="card-title" style="margin:0">${entity.label || name}</div>
                    <div style="font-size:12px;color:var(--text-muted)">${name} · ${entity.description || ''}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:4px">实体别名: ${escHtml(entityAliases || '-')}</div>
                </div>
                <div style="display:flex;gap:6px">
                    <button class="btn btn-secondary btn-sm" onclick="showEditEntityModalByKey('${entityKey}')">编辑</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteEntityActionByKey('${entityKey}')">删除</button>
                </div>
            </div>
            <div class="card-title" style="font-size:13px;margin-top:16px">属性列表</div>
            ${propsHtml}
            <button class="btn btn-secondary btn-sm" onclick="showAddPropertyModalByKey('${entityKey}')" style="margin-top:10px">＋ 添加属性</button>
        </div>`;
}

function selectEntityByKey(entityKey) {
    return selectEntity(decodeURIComponent(entityKey));
}

function showEditEntityModalByKey(entityKey) {
    return showEditEntityModal(decodeURIComponent(entityKey));
}

function deleteEntityActionByKey(entityKey) {
    return deleteEntityAction(decodeURIComponent(entityKey));
}

function showAddPropertyModalByKey(entityKey) {
    return showAddPropertyModal(decodeURIComponent(entityKey));
}

function showEditPropertyModalByKey(entityKey, propKey) {
    return showEditPropertyModal(decodeURIComponent(entityKey), decodeURIComponent(propKey));
}

function deletePropertyByKey(entityKey, propKey) {
    return deleteProperty(decodeURIComponent(entityKey), decodeURIComponent(propKey));
}

function renderOntologyGraph(onto) {
    const container = document.getElementById('ontologyGraph');
    if (!container) return;
    const entities = Object.keys(onto.entities);
    const nodes = entities.map((name, i) => ({
        id: name, label: onto.entities[name].label || name,
        color: {
            background: ['#7c6cff', '#34d399', '#fbbf24', '#f87171', '#60a5fa'][i % 5],
            border: '#444'
        },
        font: { color: '#e8e6f0', size: 12 }, shape: 'box',
        borderWidth: 2, margin: 10
    }));
    const edges = (onto.relations || []).map((r, i) => ({
        from: r.from, to: r.to, label: r.label,
        color: { color: 'rgba(120,100,255,0.4)' },
        font: { color: '#6b6785', size: 10 },
        arrows: 'to', smooth: { type: 'curvedCW', roundness: 0.2 }
    }));
    ontologyGraph = new vis.Network(container, { nodes, edges }, {
        physics: { enabled: true, solver: 'repulsion' },
        interaction: { hover: true }
    });
}

function renderRelationList(relations) {
    const el = document.getElementById('relationList');
    if (!relations || !relations.length) { el.innerHTML = '<div style="color:var(--text-muted);padding:12px">无关系</div>'; return; }
    let html = '<div class="table-scroll relation-table-scroll"><table class="data-table"><thead><tr><th>源实体</th><th>目标实体</th><th>关系</th><th>关联字段</th><th>操作</th></tr></thead><tbody>';
    relations.forEach((r, i) => {
        const joinFields = (r.from_field && r.to_field)
            ? `${escHtml(r.from_field)} = ${escHtml(r.to_field)}`
            : '-';
        html += `<tr><td>${r.from}</td><td>${r.to}</td><td>${r.label} (${r.type})</td><td>${joinFields}</td>
            <td>
                <button class="btn btn-secondary btn-sm" onclick="showEditRelationModal(${i})">编辑</button>
                <button class="btn btn-danger btn-sm" onclick="deleteRelationAction(${i})">删除</button>
            </td></tr>`;
    });
    html += '</tbody></table></div>';
    el.innerHTML = html;
}

// 本体 CRUD 操作
function showAddEntityModal() {
    showModal('新增实体', `
        <div class="form-row"><label>实体名(英文)</label><input class="form-input" id="m-ename"></div>
        <div class="form-row"><label>中文标签</label><input class="form-input" id="m-elabel"></div>
        <div class="form-row"><label>描述</label><input class="form-input" id="m-edesc"></div>
        <div class="form-row"><label>实体别名(逗号分隔)</label><input class="form-input" id="m-ealiases" placeholder="如: 学员,在校生"></div>
    `, async () => {
        await API.addEntity({
            name: gv('m-ename'),
            label: gv('m-elabel'),
            description: gv('m-edesc'),
            aliases: parseAliasList(gv('m-ealiases'))
        });
        closeModal(); loadOntologyModule(); showToast('添加成功', 'success');
    });
}

async function showEditEntityModal(entityName) {
    try {
        const entity = await API.request(`/api/ontology/entities/${entityName}`);
        showModal(`编辑实体: ${entityName}`, `
            <div class="form-row"><label>中文标签</label><input class="form-input" id="m-elabel" value="${escHtml(entity.label || '')}"></div>
            <div class="form-row"><label>描述</label><input class="form-input" id="m-edesc" value="${escHtml(entity.description || '')}"></div>
            <div class="form-row"><label>实体别名(逗号分隔)</label><input class="form-input" id="m-ealiases" value="${escHtml(stringifyAliasList(entity.aliases))}" placeholder="如: 学员,在校生"></div>
        `, async () => {
            await API.updateEntity(entityName, {
                label: gv('m-elabel'),
                description: gv('m-edesc'),
                aliases: parseAliasList(gv('m-ealiases'))
            });
            closeModal();
            loadOntologyModule();
            selectEntity(entityName);
            showToast('更新成功', 'success');
        });
    } catch (e) {
        showToast('加载实体失败: ' + e.message, 'error');
    }
}

function buildEntityOptions(entityNames, selected = '') {
    return entityNames
        .map(name => `<option value="${name}" ${name === selected ? 'selected' : ''}>${name}</option>`)
        .join('');
}

function updateRelationFieldOptions(onto, fromField = '', toField = '') {
    const fromEntity = gv('m-rfrom');
    const toEntity = gv('m-rto');
    const fromProps = Object.keys((onto.entities?.[fromEntity]?.properties) || {});
    const toProps = Object.keys((onto.entities?.[toEntity]?.properties) || {});
    const fromFieldEl = document.getElementById('m-rfrom-field');
    const toFieldEl = document.getElementById('m-rto-field');
    if (!fromFieldEl || !toFieldEl) return;

    fromFieldEl.innerHTML = '<option value="">(可选)</option>' + fromProps
        .map(p => `<option value="${p}" ${p === fromField ? 'selected' : ''}>${p}</option>`)
        .join('');
    toFieldEl.innerHTML = '<option value="">(可选)</option>' + toProps
        .map(p => `<option value="${p}" ${p === toField ? 'selected' : ''}>${p}</option>`)
        .join('');
}

async function showAddRelationModal() {
    const onto = await API.getOntology();
    const entityNames = Object.keys(onto.entities || {});
    showModal('新增关系', `
        <div class="form-row"><label>源实体</label><select class="form-input form-select" id="m-rfrom">${buildEntityOptions(entityNames)}</select></div>
        <div class="form-row"><label>目标实体</label><select class="form-input form-select" id="m-rto">${buildEntityOptions(entityNames)}</select></div>
        <div class="form-row"><label>关系类型</label><input class="form-input" id="m-rtype" placeholder="如: belongs_to"></div>
        <div class="form-row"><label>中文标签</label><input class="form-input" id="m-rlabel" placeholder="如: 属于"></div>
        <div class="form-row"><label>源关联字段(可选)</label><select class="form-input form-select" id="m-rfrom-field"></select></div>
        <div class="form-row"><label>目标关联字段(可选)</label><select class="form-input form-select" id="m-rto-field"></select></div>
    `, async () => {
        await API.addRelation({
            from: gv('m-rfrom'),
            to: gv('m-rto'),
            type: gv('m-rtype'),
            label: gv('m-rlabel'),
            from_field: gv('m-rfrom-field'),
            to_field: gv('m-rto-field')
        });
        closeModal(); loadOntologyModule(); showToast('添加成功', 'success');
    });
    updateRelationFieldOptions(onto);
    document.getElementById('m-rfrom').addEventListener('change', () => updateRelationFieldOptions(onto));
    document.getElementById('m-rto').addEventListener('change', () => updateRelationFieldOptions(onto));
}

async function showEditRelationModal(index) {
    const [onto, relations] = await Promise.all([API.getOntology(), API.getRelations()]);
    const relation = (relations || [])[index];
    if (!relation) {
        showToast('关系不存在', 'error');
        return;
    }
    const entityNames = Object.keys(onto.entities || {});
    showModal('编辑关系', `
        <div class="form-row"><label>源实体</label><select class="form-input form-select" id="m-rfrom">${buildEntityOptions(entityNames, relation.from || '')}</select></div>
        <div class="form-row"><label>目标实体</label><select class="form-input form-select" id="m-rto">${buildEntityOptions(entityNames, relation.to || '')}</select></div>
        <div class="form-row"><label>关系类型</label><input class="form-input" id="m-rtype" value="${escHtml(relation.type || '')}" placeholder="如: belongs_to"></div>
        <div class="form-row"><label>中文标签</label><input class="form-input" id="m-rlabel" value="${escHtml(relation.label || '')}" placeholder="如: 属于"></div>
        <div class="form-row"><label>源关联字段(可选)</label><select class="form-input form-select" id="m-rfrom-field"></select></div>
        <div class="form-row"><label>目标关联字段(可选)</label><select class="form-input form-select" id="m-rto-field"></select></div>
    `, async () => {
        await API.updateRelation(index, {
            from: gv('m-rfrom'),
            to: gv('m-rto'),
            type: gv('m-rtype'),
            label: gv('m-rlabel'),
            from_field: gv('m-rfrom-field'),
            to_field: gv('m-rto-field')
        });
        closeModal(); loadOntologyModule(); showToast('更新成功', 'success');
    });
    updateRelationFieldOptions(onto, relation.from_field || '', relation.to_field || '');
    document.getElementById('m-rfrom').addEventListener('change', () => updateRelationFieldOptions(onto));
    document.getElementById('m-rto').addEventListener('change', () => updateRelationFieldOptions(onto));
}

function showAddPropertyModal(entityName) {
    showModal(`为 ${entityName} 添加属性`, `
        <div class="form-row"><label>属性名</label><input class="form-input" id="m-pname"></div>
        <div class="form-row"><label>中文标签</label><input class="form-input" id="m-plabel"></div>
        <div class="form-row"><label>描述</label><input class="form-input" id="m-pdesc"></div>
        <div class="form-row"><label>类型</label><select class="form-input form-select" id="m-ptype">
            <option value="string">string</option><option value="number">number</option>
            <option value="date">date</option><option value="boolean">boolean</option>
        </select></div>
        <div class="form-row"><label>属性别名(逗号分隔)</label><input class="form-input" id="m-paliases" placeholder="如: 地区,辖区"></div>
        <div class="form-row"><label>值别名(JSON，可选)</label><textarea class="form-input" id="m-pvaluealiases" rows="4" placeholder='{"省内":"江苏省","本省":"江苏省"}'></textarea></div>
    `, async () => {
        const onto = await API.getOntology();
        const entity = onto.entities[entityName];
        entity.properties[gv('m-pname')] = {
            label: gv('m-plabel'),
            description: gv('m-pdesc'),
            type: gv('m-ptype'),
            aliases: parseAliasList(gv('m-paliases')),
            value_aliases: parseJsonObject(gv('m-pvaluealiases'))
        };
        await API.updateEntity(entityName, { properties: entity.properties });
        closeModal(); selectEntity(entityName); showToast('添加成功', 'success');
    });
}

async function showEditPropertyModal(entityName, propName) {
    try {
        const onto = await API.getOntology();
        const entity = onto.entities[entityName];
        if (!entity || !entity.properties || !entity.properties[propName]) {
            showToast('属性不存在', 'error');
            return;
        }
        const prop = entity.properties[propName];
        showModal(`编辑属性: ${entityName}.${propName}`, `
            <div class="form-row"><label>中文标签</label><input class="form-input" id="m-plabel" value="${escHtml(prop.label || '')}"></div>
            <div class="form-row"><label>描述</label><input class="form-input" id="m-pdesc" value="${escHtml(prop.description || '')}"></div>
            <div class="form-row"><label>类型</label><select class="form-input form-select" id="m-ptype">
                <option value="string" ${prop.type === 'string' ? 'selected' : ''}>string</option>
                <option value="number" ${prop.type === 'number' ? 'selected' : ''}>number</option>
                <option value="date" ${prop.type === 'date' ? 'selected' : ''}>date</option>
                <option value="boolean" ${prop.type === 'boolean' ? 'selected' : ''}>boolean</option>
            </select></div>
            <div class="form-row"><label>属性别名(逗号分隔)</label><input class="form-input" id="m-paliases" value="${escHtml(stringifyAliasList(prop.aliases))}" placeholder="如: 地区,辖区"></div>
            <div class="form-row"><label>值别名(JSON，可选)</label><textarea class="form-input" id="m-pvaluealiases" rows="4" placeholder='{"省内":"江苏省"}'>${escHtml(JSON.stringify(prop.value_aliases || {}, null, 2))}</textarea></div>
        `, async () => {
            const original = entity.properties[propName] || {};
            entity.properties[propName] = {
                ...original,
                label: gv('m-plabel'),
                description: gv('m-pdesc'),
                type: gv('m-ptype'),
                aliases: parseAliasList(gv('m-paliases')),
                value_aliases: parseJsonObject(gv('m-pvaluealiases'))
            };
            await API.updateEntity(entityName, { properties: entity.properties });
            closeModal();
            selectEntity(entityName);
            showToast('更新成功', 'success');
        });
    } catch (e) {
        showToast('加载属性失败: ' + e.message, 'error');
    }
}

async function deleteEntityAction(name) {
    if (!confirm(`确定删除实体 "${name}"？\n这将断开所有相关映射。`)) return;
    const res = await API.deleteEntity(name);
    if (res.mapping_impacts?.length) showToast(`已断开 ${res.mapping_impacts.length} 个映射`, 'info');
    loadOntologyModule();
    document.getElementById('entityDetail').innerHTML = '<div style="padding:20px;color:var(--text-muted)">实体已删除</div>';
}

async function deleteProperty(entity, prop) {
    if (!confirm(`确定删除属性 "${prop}"？`)) return;
    await API.deleteProperty(entity, prop);
    selectEntity(entity); showToast('已删除', 'success');
}

async function deleteRelationAction(index) {
    if (!confirm('确定删除此关系？')) return;
    await API.deleteRelation(index);
    loadOntologyModule(); showToast('已删除', 'success');
}

// ============================================================
// 2. 鏁版嵁琛ㄧ鐞?
// ============================================================
async function loadTablesModule() {
    try {
        await loadTableStructure();
    } catch (e) { showToast('加载失败', 'error'); }
}

async function loadTableStructure() {
    try {
        const tables = await API.getTables();
        const el = document.getElementById('tableStructure');
        if (!tables.length) { el.innerHTML = '<div style="color:var(--text-muted);padding:20px">无数据表</div>'; return; }
        el.innerHTML = tables.map(t => `
            <div class="card">
                <div class="card-title">${t.table_name} ${t.mapped_entity ? `<span class="badge badge-blue">→ ${t.mapped_entity}</span>` : ''}</div>
                <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px">${t.file_name}</div>
                <table class="data-table">
                    <thead><tr><th>字段名</th><th>类型</th></tr></thead>
                    <tbody>${t.columns.map(c => `<tr><td>${c.name}</td><td>${c.type}</td></tr>`).join('')}</tbody>
                </table>
            </div>`).join('');
    } catch (e) { showToast('加载失败', 'error'); }
}

// ============================================================
// 3. 映射管理
// ============================================================
async function loadMappingModule() {
    try {
        await loadMappingForSource();
    } catch (e) { showToast('加载失败', 'error'); }
}

async function loadMappingForSource() {
    try {
        const mapping = await API.getMapping();
        const el = document.getElementById('mappingList');
        const tm = mapping.table_mappings || {};
        if (!Object.keys(tm).length) { el.innerHTML = '<div style="color:var(--text-muted);padding:20px">无映射</div>'; return; }

        let html = '';
        for (const [entity, m] of Object.entries(tm)) {
            const fm = m.field_mappings || {};
            const fieldSemantics = m.field_value_semantics || m.value_semantics || {};
            const semanticsCount = Object.keys(fieldSemantics).length;
            html += `<div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                    <div class="card-title" style="margin:0">${entity} → ${m.table_name}
                        <span class="badge badge-yellow" style="margin-left:8px">值语义字段 ${semanticsCount}</span>
                    </div>
                    <div style="display:flex;gap:6px">
                        <button class="btn btn-primary btn-sm" onclick="llmAnalyze('${entity}','${m.file_name}')">LLM 预分析</button>
                        <button class="btn btn-secondary btn-sm" onclick="showEditMappingModal('${entity}')">编辑</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteMappingAction('${entity}')">删除</button>
                    </div>
                </div>
                <div class="mapping-editor">
                    <div class="mapping-col">
                        <div class="mapping-col-title">本体属性</div>
                        ${Object.keys(fm).map(p => `<div class="mapping-item mapped">${p}</div>`).join('')}
                    </div>
                    <div class="mapping-arrow">→</div>
                    <div class="mapping-col">
                        <div class="mapping-col-title">表字段</div>
                        ${Object.values(fm).map(f => `<div class="mapping-item mapped">${f}</div>`).join('')}
                    </div>
                </div>
            </div>`;
        }
        el.innerHTML = html;
    } catch (e) {
        document.getElementById('mappingList').innerHTML = '<div style="color:var(--text-muted);padding:20px">加载失败</div>';
    }
}

async function llmAnalyze(entityName, fileName) {
    showToast('正在调用 LLM 预分析...', 'info');
    try {
        const result = await API.llmAnalyze({ entity_name: entityName, file_name: fileName });
        if (result.success) {
            const suggested = result.suggested_mapping;
            showModal('LLM 预分析结果', `
                <div style="margin-bottom:16px;color:var(--text-muted);font-size:13px">
                    以下是 LLM 推荐的映射，您可以修改后保存：
                </div>
                <table class="data-table">
                    <thead><tr><th>本体属性</th><th>推荐字段</th></tr></thead>
                    <tbody>${Object.entries(suggested).map(([k, v]) =>
                `<tr><td>${k}</td><td><input class="form-input" value="${v}" data-prop="${k}" style="padding:4px 8px"></td></tr>`
            ).join('')}</tbody>
                </table>
            `, async () => {
                const inputs = document.querySelectorAll('.modal [data-prop]');
                const fm = {};
                inputs.forEach(inp => { if (inp.value) fm[inp.dataset.prop] = inp.value; });
                await API.updateEntityMapping(entityName, {
                    table_name: fileName.replace('.xlsx', ''),
                    file_name: fileName,
                    field_mappings: fm
                });
                closeModal(); loadMappingForSource(); showToast('映射已更新', 'success');
            });
        }
    } catch (e) { showToast(e.message, 'error'); }
}

async function deleteMappingAction(entity) {
    if (!confirm(`确定删除 ${entity} 的映射？`)) return;
    await API.deleteEntityMapping(entity);
    loadMappingForSource(); showToast('已删除', 'success');
}

function showAddMappingModal() {
    showModal('新增映射', `
        <div class="form-row"><label>实体名</label><input class="form-input" id="m-mentity" placeholder="如: Student"></div>
        <div class="form-row"><label>表名</label><input class="form-input" id="m-mtable" placeholder="如: xuesheng"></div>
        <div class="form-row"><label>文件名</label><input class="form-input" id="m-mfile" placeholder="如: xuesheng.xlsx"></div>
    `, async () => {
        await API.updateEntityMapping(gv('m-mentity'), {
            table_name: gv('m-mtable'), file_name: gv('m-mfile'), field_mappings: {}
        });
        closeModal(); showToast('创建成功，请使用LLM预分析或手动编辑字段映射', 'success');
    });
}

async function showEditMappingModal(entityName) {
    try {
        const [mapping, tables] = await Promise.all([
            API.getMapping(),
            API.getTables()
        ]);
        const current = (mapping.table_mappings || {})[entityName];
        if (!current) {
            showToast('映射不存在', 'error');
            return;
        }

        const tableNames = (tables || []).map(t => t.table_name).filter(Boolean);
        const fileNames = (tables || []).map(t => t.file_name).filter(Boolean);
        const tableHint = tableNames.length ? `可选表: ${tableNames.join(', ')}` : '未发现表结构';
        const fileHint = fileNames.length ? `可选文件: ${fileNames.join(', ')}` : '未发现文件';
        const fieldSemantics = current.field_value_semantics || current.value_semantics || {};

        showModal(`编辑映射: ${entityName}`, `
            <div class="form-row"><label>表名</label><input class="form-input" id="m-mtable" value="${escHtml(current.table_name || '')}"></div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:-8px;margin-bottom:8px">${escHtml(tableHint)}</div>
            <div class="form-row"><label>文件名</label><input class="form-input" id="m-mfile" value="${escHtml(current.file_name || '')}"></div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:-8px;margin-bottom:8px">${escHtml(fileHint)}</div>
            <div class="form-row">
                <label>字段映射(JSON)</label>
                <textarea class="form-input" id="m-mfieldmap" rows="10">${escHtml(JSON.stringify(current.field_mappings || {}, null, 2))}</textarea>
            </div>
            <div class="form-row">
                <label>字段值语义(JSON，数据层)</label>
                <textarea class="form-input" id="m-mvaluesem" rows="12" placeholder='{"IS_UPDATE_ON_TIME":{"closed_set":["是","否"],"labels":{"是":"按时更新","否":"未按时更新"},"aliases":{"按时更新":"是","未按时更新":"否"}}}'>${escHtml(JSON.stringify(fieldSemantics, null, 2))}</textarea>
            </div>
        `, async () => {
            const fieldMappings = parseJsonObjectWithLabel(gv('m-mfieldmap'), '字段映射JSON');
            const fieldValueSemantics = parseJsonObjectWithLabel(gv('m-mvaluesem'), '字段值语义JSON');
            await API.updateEntityMapping(entityName, {
                table_name: gv('m-mtable'),
                file_name: gv('m-mfile'),
                field_mappings: fieldMappings,
                field_value_semantics: fieldValueSemantics
            });
            closeModal();
            loadMappingForSource();
            showToast('映射已更新', 'success');
        });
    } catch (e) {
        showToast(e.message || '加载映射失败', 'error');
    }
}

// ============================================================
// 4. 数据管理
// ============================================================
let dataPage = 1;
async function loadDataModule() {
    try {
        await loadDataEntities();
    } catch (e) { }
}

async function loadDataEntities() {
    const sel = document.getElementById('dataEntitySelect');
    try {
        const mapping = await API.getMapping();
        const entities = Object.keys(mapping.table_mappings || {});
        sel.innerHTML = '<option value="">请选择</option>' + entities.map(e => `<option value="${e}">${e}</option>`).join('');
    } catch (e) { sel.innerHTML = ''; }
}

async function loadDataTable() {
    const entity = document.getElementById('dataEntitySelect').value;
    const search = document.getElementById('dataSearch').value;
    if (!entity) return;
    try {
        const result = await API.getData(entity, dataPage, 15, search);
        let html = '<table class="data-table"><thead><tr>';
        html += '<th>#</th>';
        result.columns.forEach(c => html += `<th>${c}</th>`);
        html += '<th>操作</th></tr></thead><tbody>';
        result.data.forEach((row, i) => {
            const absIdx = (result.page - 1) * result.page_size + i;
            html += '<tr>';
            html += `<td>${absIdx + 1}</td>`;
            result.columns.forEach(c => html += `<td>${row[c] !== undefined ? row[c] : ''}</td>`);
            html += `<td>
                <button class="btn btn-secondary btn-sm" onclick="showEditDataModal('${entity}',${absIdx},${JSON.stringify(row).replace(/"/g, '&quot;')})">编辑</button>
                <button class="btn btn-danger btn-sm" onclick="deleteDataAction('${entity}',${absIdx})">删除</button>
            </td></tr>`;
        });
        html += '</tbody></table>';

        // 分页
        const totalPages = Math.ceil(result.total / result.page_size);
        html += `<div class="pagination">
            <button class="page-btn" onclick="dataPage=Math.max(1,dataPage-1);loadDataTable()" ${dataPage <= 1 ? 'disabled' : ''}>上一页</button>
            <span class="page-info">${result.page} / ${totalPages} (共 ${result.total} 条)</span>
            <button class="page-btn" onclick="dataPage=Math.min(${totalPages},dataPage+1);loadDataTable()" ${dataPage >= totalPages ? 'disabled' : ''}>下一页</button>
        </div>`;

        document.getElementById('dataTableContainer').innerHTML = html;
    } catch (e) { showToast('加载失败: ' + e.message, 'error'); }
}

function showAddDataModal() {
    const entity = document.getElementById('dataEntitySelect').value;
    if (!entity) { showToast('请先选择实体', 'error'); return; }
    // 获取列名
    API.getData(entity, 1, 1, '').then(result => {
        const fields = result.columns.map(c => `<div class="form-row"><label>${c}</label><input class="form-input" data-col="${c}"></div>`).join('');
        showModal('新增数据', fields, async () => {
            const row = {};
            document.querySelectorAll('.modal [data-col]').forEach(inp => { if (inp.value) row[inp.dataset.col] = inp.value; });
            await API.addData(entity, row);
            closeModal(); loadDataTable(); showToast('添加成功', 'success');
        });
    });
}

function showEditDataModal(entity, idx, row) {
    const fields = Object.entries(row).map(([k, v]) => `<div class="form-row"><label>${k}</label><input class="form-input" data-col="${k}" value="${v || ''}"></div>`).join('');
    showModal('编辑数据', fields, async () => {
        const updated = {};
        document.querySelectorAll('.modal [data-col]').forEach(inp => { updated[inp.dataset.col] = inp.value; });
        await API.updateData(entity, idx, updated);
        closeModal(); loadDataTable(); showToast('更新成功', 'success');
    });
}

async function deleteDataAction(entity, idx) {
    if (!confirm('确定删除此行？')) return;
    await API.deleteData(entity, idx);
    loadDataTable(); showToast('已删除', 'success');
}

// ============================================================
// 5. DSL 日志
// ============================================================
async function loadDSLLogs() {
    try {
        const search = document.getElementById('dslSearch')?.value || '';
        const logs = await API.getDSLLogs(search);
        const el = document.getElementById('dslCards');
        if (!logs.length) { el.innerHTML = '<div style="color:var(--text-muted);padding:40px;text-align:center">暂无 DSL 日志</div>'; return; }
        el.innerHTML = logs.map(l => `
            <div class="dsl-card" onclick="toggleDSLCard(this)">
                <div class="dsl-card-header">
                    <div class="dsl-card-question">${escHtml(l.question)}</div>
                    <span class="dsl-card-status ${l.status}">${l.status === 'success' ? '成功' : '空结果'}</span>
                </div>
                <div class="dsl-card-meta">
                    <span>🕐 ${l.timestamp}</span>
                    <span>👤 ${l.user || ''}</span>
                    <span>📋 ${(l.target_entities || []).join(', ')}</span>
                    <span>📊 ${l.calc_type || ''}</span>
                </div>
                <div class="dsl-card-expand">
                    <pre>${escHtml(JSON.stringify(l.dsl, null, 2))}</pre>
                </div>
            </div>`).join('');
    } catch (e) { showToast('加载失败', 'error'); }
}

function toggleDSLCard(card) {
    card.querySelector('.dsl-card-expand').classList.toggle('open');
}

// ============================================================
// 6. MCP 管理
// ============================================================
async function loadMCPs() {
    try {
        const mcps = await API.getMCPs();
        const el = document.getElementById('mcpList');
        if (!mcps.length) { el.innerHTML = '<div style="color:var(--text-muted);padding:40px;text-align:center">暂无 MCP 配置</div>'; return; }
        el.innerHTML = mcps.map(m => `
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <div>
                        <div class="card-title" style="margin:0">${m.name || 'MCP'} <span class="badge badge-green">${m.status || 'active'}</span></div>
                        <div style="font-size:12px;color:var(--text-muted)">${m.description || ''}</div>
                    </div>
                    <div style="display:flex;gap:6px">
                        <button class="btn btn-secondary btn-sm" onclick="testMCPAction(${JSON.stringify(m).replace(/"/g, '&quot;')})">🔌 测试</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteMCPAction('${m.id}')">删除</button>
                    </div>
                </div>
                ${m.config ? `<pre style="margin-top:10px;background:var(--bg-tertiary);padding:10px;border-radius:8px;font-size:12px;color:var(--text-secondary);max-height:150px;overflow-y:auto">${escHtml(typeof m.config === 'string' ? m.config : JSON.stringify(m.config, null, 2))}</pre>` : ''}
            </div>`).join('');
    } catch (e) { showToast('加载失败', 'error'); }
}

function showAddMCPModal() {
    showModal('新增 MCP', `
        <div class="form-row"><label>名称</label><input class="form-input" id="m-mcpname"></div>
        <div class="form-row"><label>描述</label><input class="form-input" id="m-mcpdesc"></div>
        <div class="form-row"><label>配置 (JSON)</label><textarea class="form-input" id="m-mcpconfig" rows="8" placeholder='{"endpoint": "...", "method": "..."}'></textarea></div>
    `, async () => {
        let cfg = gv('m-mcpconfig');
        try { cfg = JSON.parse(cfg); } catch (e) { }
        await API.addMCP({ name: gv('m-mcpname'), description: gv('m-mcpdesc'), config: cfg });
        closeModal(); loadMCPs(); showToast('添加成功', 'success');
    });
}

async function testMCPAction(mcp) {
    try {
        const res = await API.testMCP(mcp);
        showToast(res.message, 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

async function deleteMCPAction(id) {
    if (!confirm('确定删除该 MCP？')) return;
    await API.deleteMCP(id);
    loadMCPs(); showToast('已删除', 'success');
}

// ============================================================
// 7. 数据库连接配置
// ============================================================
async function loadDBConfigModule() {
    try {
        const result = await API.getDBConfig();
        document.getElementById('dbStoreType').value = result.store_type || 'sheet';
        document.getElementById('dbHost').value = result.mysql?.host || '';
        document.getElementById('dbPort').value = result.mysql?.port || 3306;
        document.getElementById('dbUser').value = result.mysql?.user || '';
        document.getElementById('dbPassword').value = '';
        document.getElementById('dbDatabase').value = result.mysql?.database || '';
        document.getElementById('dbKeepPassword').checked = true;

        const hint = document.getElementById('dbConfigHint');
        if (result.mysql?.has_password) {
            hint.textContent = '当前已保存数据库密码（页面不回显）。';
        } else {
            hint.textContent = '当前未保存数据库密码。';
        }
    } catch (e) {
        showToast('加载数据库配置失败', 'error');
    }
}

function collectDBConfigForm() {
    return {
        store_type: gv('dbStoreType') || 'sheet',
        keep_password: document.getElementById('dbKeepPassword')?.checked ?? true,
        mysql: {
            host: gv('dbHost'),
            port: Number(gv('dbPort') || 3306),
            user: gv('dbUser'),
            password: document.getElementById('dbPassword')?.value || '',
            database: gv('dbDatabase')
        }
    };
}

async function saveDBConfigAction() {
    try {
        const payload = collectDBConfigForm();
        await API.saveDBConfig(payload);
        showToast('数据库配置已保存', 'success');
        await loadDBConfigModule();
    } catch (e) {
        showToast(e.message || '保存失败', 'error');
    }
}

async function testDBConfigAction() {
    try {
        const payload = collectDBConfigForm();
        const result = await API.testDBConfig({ mysql: payload.mysql });
        showToast(result.message || '连接成功', 'success');
    } catch (e) {
        showToast(e.message || '连接失败', 'error');
    }
}

// ============================================================
// 8. 知识配置
// ============================================================
let knowledgeItemsCache = [];
let rewriteRulesCache = [];
let sqlRulesCache = [];
let promptItemsCache = [];

async function loadKnowledgeModule() {
    try {
        const [items, rules, sqlRules] = await Promise.all([
            API.getKnowledge(),
            API.getRewriteRules(),
            API.getSqlRules()
        ]);
        knowledgeItemsCache = Array.isArray(items) ? items : [];
        rewriteRulesCache = Array.isArray(rules) ? rules : [];
        sqlRulesCache = Array.isArray(sqlRules) ? sqlRules : [];
        const el = document.getElementById('knowledgeList');

        let html = '<div class="card-title" style="margin-bottom:10px">术语知识（解释型）</div>';
        if (!knowledgeItemsCache.length) {
            html += '<div style="color:var(--text-muted);padding:16px 0">暂无术语知识</div>';
        } else {
            html += '<table class="data-table"><thead><tr><th>术语</th><th>关键词</th><th>释义</th><th>操作</th></tr></thead><tbody>';
            knowledgeItemsCache.forEach(item => {
                const id = String(item.id || '');
                const term = escHtml(item.term || '');
                const keywords = escHtml((item.keywords || []).join(', '));
                const content = escHtml(item.content || '');
                const shortContent = content.length > 120 ? `${content.slice(0, 120)}...` : content;
                html += `<tr>
                    <td><strong>${term}</strong></td>
                    <td>${keywords || '-'}</td>
                    <td title="${content}">${shortContent}</td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="showEditKnowledgeModal('${id}')">编辑</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteKnowledgeAction('${id}')">删除</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table>';
        }

        html += '<div class="card-title" style="margin:18px 0 10px">全局同义词转换（键值型）</div>';
        if (!rewriteRulesCache.length) {
            html += '<div style="color:var(--text-muted);padding:16px 0">暂无全局同义词转换规则</div>';
        } else {
            html += '<table class="data-table"><thead><tr><th>原词</th><th>目标词</th><th>说明</th><th>启用</th><th>操作</th></tr></thead><tbody>';
            rewriteRulesCache.forEach(item => {
                const id = String(item.id || '');
                const source = escHtml(item.source || '');
                const target = escHtml(item.target || '');
                const description = escHtml(item.description || '');
                const enabled = !!item.enabled;
                html += `<tr>
                    <td><strong>${source}</strong></td>
                    <td>${target}</td>
                    <td>${description || '-'}</td>
                    <td>${enabled ? '<span class="badge badge-green">是</span>' : '<span class="badge">否</span>'}</td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="showEditRewriteRuleModal('${id}')">编辑</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteRewriteRuleAction('${id}')">删除</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table>';
        }

        html += '<div class="card-title" style="margin:18px 0 10px">指标SQL规则（执行型）</div>';
        if (!sqlRulesCache.length) {
            html += '<div style="color:var(--text-muted);padding:16px 0">暂无指标SQL规则</div>';
        } else {
            html += '<table class="data-table"><thead><tr><th>规则名</th><th>关键词</th><th>优先级</th><th>启用</th><th>SQL</th><th>操作</th></tr></thead><tbody>';
            sqlRulesCache.forEach(item => {
                const id = String(item.id || '');
                const name = escHtml(item.name || '');
                const keywords = escHtml((item.keywords || []).join(', '));
                const priority = Number(item.priority || 100);
                const enabled = !!item.enabled;
                const sql = escHtml(item.sql || '');
                const sqlShort = sql.length > 120 ? `${sql.slice(0, 120)}...` : sql;
                html += `<tr>
                    <td><strong>${name}</strong></td>
                    <td>${keywords || '-'}</td>
                    <td>${priority}</td>
                    <td>${enabled ? '<span class="badge badge-green">是</span>' : '<span class="badge">否</span>'}</td>
                    <td title="${sql}">${sqlShort}</td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="showEditSqlRuleModal('${id}')">编辑</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteSqlRuleAction('${id}')">删除</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table>';
        }

        el.innerHTML = html;
    } catch (e) {
        showToast('加载知识配置失败', 'error');
    }
}

function showAddKnowledgeModal() {
    showModal('新增术语知识', `
        <div class="form-row"><label>术语</label><input class="form-input" id="m-kterm" placeholder="如: 应编未编"></div>
        <div class="form-row"><label>关键词(逗号分隔)</label><input class="form-input" id="m-kkeywords" placeholder="如: 应编未编,应编,未编"></div>
        <div class="form-row"><label>释义(长文本)</label><textarea class="form-input" id="m-kcontent" rows="8" placeholder="请输入术语口径说明..."></textarea></div>
    `, async () => {
        await API.addKnowledge({
            term: gv('m-kterm'),
            keywords: parseAliasList(gv('m-kkeywords')),
            content: gv('m-kcontent')
        });
        closeModal();
        loadKnowledgeModule();
        showToast('知识已新增', 'success');
    });
}

async function showEditKnowledgeModal(itemId) {
    try {
        let item = knowledgeItemsCache.find(x => String(x.id) === String(itemId));
        if (!item) {
            const list = await API.getKnowledge();
            knowledgeItemsCache = Array.isArray(list) ? list : [];
            item = knowledgeItemsCache.find(x => String(x.id) === String(itemId));
        }
        if (!item) {
            showToast('知识条目不存在', 'error');
            return;
        }

        showModal(`编辑术语: ${escHtml(item.term || '')}`, `
            <div class="form-row"><label>术语</label><input class="form-input" id="m-kterm" value="${escHtml(item.term || '')}"></div>
            <div class="form-row"><label>关键词(逗号分隔)</label><input class="form-input" id="m-kkeywords" value="${escHtml((item.keywords || []).join(', '))}"></div>
            <div class="form-row"><label>释义(长文本)</label><textarea class="form-input" id="m-kcontent" rows="8">${escHtml(item.content || '')}</textarea></div>
        `, async () => {
            await API.updateKnowledge(itemId, {
                term: gv('m-kterm'),
                keywords: parseAliasList(gv('m-kkeywords')),
                content: gv('m-kcontent')
            });
            closeModal();
            loadKnowledgeModule();
            showToast('知识已更新', 'success');
        });
    } catch (e) {
        showToast('加载知识失败', 'error');
    }
}

async function deleteKnowledgeAction(itemId) {
    if (!confirm('确定删除这条术语知识？')) return;
    try {
        await API.deleteKnowledge(itemId);
        loadKnowledgeModule();
        showToast('已删除', 'success');
    } catch (e) {
        showToast(e.message || '删除失败', 'error');
    }
}

function showAddRewriteRuleModal() {
    showModal('新增全局同义词转换', `
        <div class="form-row"><label>原词</label><input class="form-input" id="m-rw-source" placeholder="如: 省内"></div>
        <div class="form-row"><label>目标词</label><input class="form-input" id="m-rw-target" placeholder="如: 江苏省"></div>
        <div class="form-row"><label>说明</label><input class="form-input" id="m-rw-desc" placeholder="可选"></div>
        <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-rw-enabled" checked> 启用</label></div>
    `, async () => {
        await API.addRewriteRule({
            source: gv('m-rw-source'),
            target: gv('m-rw-target'),
            description: gv('m-rw-desc'),
            enabled: document.getElementById('m-rw-enabled').checked
        });
        closeModal();
        loadKnowledgeModule();
        showToast('转换规则已新增', 'success');
    });
}

async function showEditRewriteRuleModal(itemId) {
    try {
        let item = rewriteRulesCache.find(x => String(x.id) === String(itemId));
        if (!item) {
            const list = await API.getRewriteRules();
            rewriteRulesCache = Array.isArray(list) ? list : [];
            item = rewriteRulesCache.find(x => String(x.id) === String(itemId));
        }
        if (!item) {
            showToast('规则不存在', 'error');
            return;
        }

        showModal(`编辑转换规则: ${escHtml(item.source || '')}`, `
            <div class="form-row"><label>原词</label><input class="form-input" id="m-rw-source" value="${escHtml(item.source || '')}"></div>
            <div class="form-row"><label>目标词</label><input class="form-input" id="m-rw-target" value="${escHtml(item.target || '')}"></div>
            <div class="form-row"><label>说明</label><input class="form-input" id="m-rw-desc" value="${escHtml(item.description || '')}"></div>
            <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-rw-enabled" ${item.enabled !== false ? 'checked' : ''}> 启用</label></div>
        `, async () => {
            await API.updateRewriteRule(itemId, {
                source: gv('m-rw-source'),
                target: gv('m-rw-target'),
                description: gv('m-rw-desc'),
                enabled: document.getElementById('m-rw-enabled').checked
            });
            closeModal();
            loadKnowledgeModule();
            showToast('转换规则已更新', 'success');
        });
    } catch (e) {
        showToast('加载转换规则失败', 'error');
    }
}

async function deleteRewriteRuleAction(itemId) {
    if (!confirm('确定删除这条转换规则？')) return;
    try {
        await API.deleteRewriteRule(itemId);
        loadKnowledgeModule();
        showToast('已删除', 'success');
    } catch (e) {
        showToast(e.message || '删除失败', 'error');
    }
}

function showAddSqlRuleModal() {
    showModal('新增指标SQL规则', `
        <div class="form-row"><label>规则名</label><input class="form-input" id="m-sql-name" placeholder="如: 上云率"></div>
        <div class="form-row"><label>关键词(逗号分隔)</label><input class="form-input" id="m-sql-keywords" placeholder="如: 上云率,系统上云率"></div>
        <div class="form-row"><label>目标实体(逗号分隔，可选)</label><input class="form-input" id="m-sql-target-entities" placeholder="如: INFORMATION_SYSTEM"></div>
        <div class="form-row"><label>优先级(越大越优先)</label><input class="form-input" id="m-sql-priority" type="number" value="100"></div>
        <div class="form-row"><label>SQL(仅单条SELECT)</label><textarea class="form-input" id="m-sql-text" rows="8" placeholder="SELECT ..."></textarea></div>
        <div class="form-row"><label>说明</label><input class="form-input" id="m-sql-desc" placeholder="可选"></div>
        <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-sql-enabled" checked> 启用</label></div>
    `, async () => {
        await API.addSqlRule({
            name: gv('m-sql-name'),
            keywords: parseAliasList(gv('m-sql-keywords')),
            target_entities: parseAliasList(gv('m-sql-target-entities')),
            priority: Number(gv('m-sql-priority') || 100),
            sql: gv('m-sql-text'),
            description: gv('m-sql-desc'),
            enabled: document.getElementById('m-sql-enabled').checked
        });
        closeModal();
        loadKnowledgeModule();
        showToast('指标SQL规则已新增', 'success');
    });
}

async function showEditSqlRuleModal(itemId) {
    try {
        let item = sqlRulesCache.find(x => String(x.id) === String(itemId));
        if (!item) {
            const list = await API.getSqlRules();
            sqlRulesCache = Array.isArray(list) ? list : [];
            item = sqlRulesCache.find(x => String(x.id) === String(itemId));
        }
        if (!item) {
            showToast('规则不存在', 'error');
            return;
        }

        showModal(`编辑指标SQL规则: ${escHtml(item.name || '')}`, `
            <div class="form-row"><label>规则名</label><input class="form-input" id="m-sql-name" value="${escHtml(item.name || '')}"></div>
            <div class="form-row"><label>关键词(逗号分隔)</label><input class="form-input" id="m-sql-keywords" value="${escHtml((item.keywords || []).join(', '))}"></div>
            <div class="form-row"><label>目标实体(逗号分隔，可选)</label><input class="form-input" id="m-sql-target-entities" value="${escHtml((item.target_entities || []).join(', '))}"></div>
            <div class="form-row"><label>优先级(越大越优先)</label><input class="form-input" id="m-sql-priority" type="number" value="${Number(item.priority || 100)}"></div>
            <div class="form-row"><label>SQL(仅单条SELECT)</label><textarea class="form-input" id="m-sql-text" rows="8">${escHtml(item.sql || '')}</textarea></div>
            <div class="form-row"><label>说明</label><input class="form-input" id="m-sql-desc" value="${escHtml(item.description || '')}"></div>
            <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-sql-enabled" ${item.enabled !== false ? 'checked' : ''}> 启用</label></div>
        `, async () => {
            await API.updateSqlRule(itemId, {
                name: gv('m-sql-name'),
                keywords: parseAliasList(gv('m-sql-keywords')),
                target_entities: parseAliasList(gv('m-sql-target-entities')),
                priority: Number(gv('m-sql-priority') || 100),
                sql: gv('m-sql-text'),
                description: gv('m-sql-desc'),
                enabled: document.getElementById('m-sql-enabled').checked
            });
            closeModal();
            loadKnowledgeModule();
            showToast('指标SQL规则已更新', 'success');
        });
    } catch (e) {
        showToast('加载指标SQL规则失败', 'error');
    }
}

async function deleteSqlRuleAction(itemId) {
    if (!confirm('确定删除这条指标SQL规则？')) return;
    try {
        await API.deleteSqlRule(itemId);
        loadKnowledgeModule();
        showToast('已删除', 'success');
    } catch (e) {
        showToast(e.message || '删除失败', 'error');
    }
}

// ============================================================
// 9. 提示词配置
// ============================================================
async function loadPromptsModule() {
    try {
        const [items, llmConfig] = await Promise.all([
            API.getPrompts(),
            API.getPromptLLMConfig()
        ]);
        promptItemsCache = Array.isArray(items) ? items : [];
        const el = document.getElementById('promptList');
        if (!el) return;

        const baseUrl = escHtml(llmConfig?.base_url || '');
        const model = escHtml(llmConfig?.model || '');
        const apiKey = escHtml(llmConfig?.api_key || '');
        const effectiveBaseUrl = escHtml(llmConfig?.effective?.base_url || '');
        const effectiveModel = escHtml(llmConfig?.effective?.model || '');
        const effectiveApiKey = escHtml(llmConfig?.effective?.api_key_masked || '');

        let html = `
            <div class="card" style="margin-bottom:12px;padding:12px;">
                <div style="font-weight:600;margin-bottom:8px;">模型配置（运行时）</div>
                <div class="form-row"><label>模型地址(base_url)</label><input class="form-input" id="prompt-llm-base-url" value="${baseUrl}" placeholder="留空使用 config.py 默认值"></div>
                <div class="form-row"><label>模型ID(model)</label><input class="form-input" id="prompt-llm-model" value="${model}" placeholder="留空使用 config.py 默认值"></div>
                <div class="form-row"><label>API Key</label><input class="form-input" id="prompt-llm-api-key" value="${apiKey}" placeholder="留空使用 config.py 默认值"></div>
                <div style="font-size:12px;color:var(--text-muted);margin-top:6px;">
                    当前生效：base_url=${effectiveBaseUrl || '-'}，model=${effectiveModel || '-'}，api_key=${effectiveApiKey || '-'}
                </div>
                <div style="margin-top:10px;display:flex;gap:8px;">
                    <button class="btn btn-primary btn-sm" onclick="savePromptLLMConfig()">保存模型配置</button>
                    <button class="btn btn-secondary btn-sm" onclick="resetPromptLLMConfig()">重置为默认</button>
                </div>
            </div>
        `;

        if (!promptItemsCache.length) {
            el.innerHTML = html + '<div style="color:var(--text-muted);padding:16px 0">暂无提示词配置</div>';
            return;
        }

        html += '<table class="data-table"><thead><tr><th>键</th><th>名称</th><th>说明</th><th>模板预览</th><th>自定义</th><th>操作</th></tr></thead><tbody>';
        promptItemsCache.forEach(item => {
            const key = String(item.key || '');
            const name = escHtml(item.name || key);
            const description = escHtml(item.description || '');
            const template = escHtml(item.template || '');
            const shortTemplate = template.length > 140 ? `${template.slice(0, 140)}...` : template;
            const hasOverride = !!item.has_override;
            html += `<tr>
                <td><code>${escHtml(key)}</code></td>
                <td>${name}</td>
                <td>${description || '-'}</td>
                <td title="${template}">${shortTemplate || '-'}</td>
                <td>${hasOverride ? '<span class="badge badge-green">是</span>' : '<span class="badge">否</span>'}</td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="showEditPromptModal('${escHtml(key)}')">编辑</button>
                    <button class="btn btn-danger btn-sm" onclick="resetPromptAction('${escHtml(key)}')">重置</button>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    } catch (e) {
        showToast('加载提示词配置失败', 'error');
    }
}

function showEditPromptModal(promptKey) {
    const item = promptItemsCache.find(x => String(x.key) === String(promptKey));
    if (!item) {
        showToast('提示词不存在', 'error');
        return;
    }

    showModal(`编辑提示词: ${escHtml(item.name || promptKey)}`, `
        <div class="form-row"><label>键</label><input class="form-input" id="m-prompt-key" value="${escHtml(promptKey)}" disabled></div>
        <div class="form-row"><label>名称</label><input class="form-input" id="m-prompt-name" value="${escHtml(item.name || '')}"></div>
        <div class="form-row"><label>说明</label><input class="form-input" id="m-prompt-desc" value="${escHtml(item.description || '')}"></div>
        <div class="form-row"><label>模板</label><textarea class="form-input" id="m-prompt-template" rows="16">${escHtml(item.template || '')}</textarea></div>
        <div style="font-size:12px;color:var(--text-muted)">支持变量占位符：使用 $变量名（例如 $ontology_desc）。</div>
    `, async () => {
        await API.updatePrompt(promptKey, {
            name: gv('m-prompt-name'),
            description: gv('m-prompt-desc'),
            template: document.getElementById('m-prompt-template')?.value || ''
        });
        closeModal();
        await loadPromptsModule();
        showToast('提示词已更新', 'success');
    });
}

async function resetPromptAction(promptKey) {
    if (!confirm('确定重置为默认提示词？')) return;
    try {
        await API.resetPrompt(promptKey);
        await loadPromptsModule();
        showToast('已重置为默认值', 'success');
    } catch (e) {
        showToast(e.message || '重置失败', 'error');
    }
}

async function savePromptLLMConfig() {
    try {
        await API.savePromptLLMConfig({
            base_url: gv('prompt-llm-base-url'),
            model: gv('prompt-llm-model'),
            api_key: gv('prompt-llm-api-key')
        });
        await loadPromptsModule();
        showToast('模型配置已保存', 'success');
    } catch (e) {
        showToast(e.message || '模型配置保存失败', 'error');
    }
}

async function resetPromptLLMConfig() {
    if (!confirm('确定重置模型配置为 config.py 默认值？')) return;
    try {
        await API.resetPromptLLMConfig();
        await loadPromptsModule();
        showToast('模型配置已重置', 'success');
    } catch (e) {
        showToast(e.message || '模型配置重置失败', 'error');
    }
}

// ============================================================
// 10. 用户权限管理
// ============================================================
async function loadUsers() {
    try {
        const users = await API.getUsers();
        const el = document.getElementById('usersTable');
        let html = '<table class="data-table"><thead><tr><th>用户名</th><th>姓名</th><th>角色</th><th>可登录</th><th>可查询</th><th>管理员</th><th>操作</th></tr></thead><tbody>';
        users.forEach(u => {
            html += `<tr>
                <td><strong>${u.username}</strong></td>
                <td>${u.name}</td>
                <td><span class="badge ${u.role === 'admin' ? 'badge-blue' : u.role === 'teacher' ? 'badge-green' : 'badge-yellow'}">${u.role}</span></td>
                <td>${u.can_login ? '是' : '否'}</td>
                <td>${u.can_query ? '是' : '否'}</td>
                <td>${u.is_admin ? '是' : '否'}</td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="showEditUserModal('${u.username}')">编辑</button>
                    ${u.username !== 'admin' ? `<button class="btn btn-danger btn-sm" onclick="deleteUserAction('${u.username}')">删除</button>` : ''}
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    } catch (e) { showToast('加载失败', 'error'); }
}

function showAddUserModal() {
    showModal('新增用户', `
        <div class="form-row"><label>用户名</label><input class="form-input" id="m-uname"></div>
        <div class="form-row"><label>密码</label><input class="form-input" id="m-upwd" type="password"></div>
        <div class="form-row"><label>姓名</label><input class="form-input" id="m-udisplay"></div>
        <div class="form-row"><label>角色</label><select class="form-input form-select" id="m-urole">
            <option value="user">普通用户</option><option value="teacher">教师</option><option value="admin">管理员</option>
        </select></div>
        <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-ulogin" checked> 允许登录</label></div>
        <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-uquery"> 允许查询</label></div>
        <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-uadmin"> 管理员权限</label></div>
    `, async () => {
        await API.addUser({
            username: gv('m-uname'), password: gv('m-upwd'), name: gv('m-udisplay'),
            role: gv('m-urole'),
            can_login: document.getElementById('m-ulogin').checked,
            can_query: document.getElementById('m-uquery').checked,
            is_admin: document.getElementById('m-uadmin').checked
        });
        closeModal(); loadUsers(); showToast('添加成功', 'success');
    });
}

function showEditUserModal(username) {
    API.getUsers().then(users => {
        const u = users.find(x => x.username === username);
        if (!u) return;
        showModal(`编辑用户: ${username}`, `
            <div class="form-row"><label>姓名</label><input class="form-input" id="m-udisplay" value="${u.name}"></div>
            <div class="form-row"><label>角色</label><select class="form-input form-select" id="m-urole">
                <option value="user" ${u.role === 'user' ? 'selected' : ''}>普通用户</option>
                <option value="teacher" ${u.role === 'teacher' ? 'selected' : ''}>教师</option>
                <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>管理员</option>
            </select></div>
            <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-ulogin" ${u.can_login ? 'checked' : ''}> 允许登录</label></div>
            <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-uquery" ${u.can_query ? 'checked' : ''}> 允许查询</label></div>
            <div class="form-row"><label><input type="checkbox" class="form-checkbox" id="m-uadmin" ${u.is_admin ? 'checked' : ''}> 管理员权限</label></div>
        `, async () => {
            await API.updateUser(username, {
                name: gv('m-udisplay'), role: gv('m-urole'),
                can_login: document.getElementById('m-ulogin').checked,
                can_query: document.getElementById('m-uquery').checked,
                is_admin: document.getElementById('m-uadmin').checked
            });
            closeModal(); loadUsers(); showToast('更新成功', 'success');
        });
    });
}

async function deleteUserAction(username) {
    if (!confirm(`确定删除用户 "${username}"？`)) return;
    await API.deleteUser(username);
    loadUsers(); showToast('已删除', 'success');
}

// ============================================================
// 通用弹窗
// ============================================================
function showModal(title, contentHtml, onConfirm) {
    const container = document.getElementById('modalContainer');
    container.innerHTML = `
        <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
            <div class="modal">
                <div class="modal-title">${title}</div>
                ${contentHtml}
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                    <button class="btn btn-primary" id="modalConfirmBtn">确定</button>
                </div>
            </div>
        </div>`;
    document.getElementById('modalConfirmBtn').onclick = async () => {
        try { await onConfirm(); } catch (e) { showToast(e.message, 'error'); }
    };
}

function closeModal() {
    document.getElementById('modalContainer').innerHTML = '';
}

function gv(id) { return document.getElementById(id)?.value?.trim() || ''; }
function parseAliasList(text) {
    if (!text) return [];
    return text
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
        .filter((v, i, arr) => arr.indexOf(v) === i);
}
function stringifyAliasList(aliases) {
    if (!Array.isArray(aliases) || aliases.length === 0) return '';
    return aliases.join(', ');
}
function parseJsonObject(text) {
    return parseJsonObjectWithLabel(text, 'JSON');
}

function parseJsonObjectWithLabel(text, label) {
    if (!text || !text.trim()) return {};
    try {
        const parsed = JSON.parse(text);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('must be json object');
        }
        return parsed;
    } catch (e) {
        throw new Error(`${label} 格式错误`);
    }
}
function escHtml(t) { if (!t) return ''; const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

