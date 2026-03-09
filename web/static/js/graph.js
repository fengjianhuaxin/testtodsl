/* ============ 知识图谱可视化 ============ */
let graphNetwork = null;
let graphData = { nodes: [], edges: [] };
let isGraphLoaded = false;

const ENTITY_COLORS = {
    'Student': { background: '#7c6cff', border: '#5b4cc4' },
    'Teacher': { background: '#34d399', border: '#059669' },
    'Course': { background: '#fbbf24', border: '#d97706' },
    'Score': { background: '#f87171', border: '#dc2626' },
    'Class': { background: '#60a5fa', border: '#2563eb' }
};

const SOURCE_SHAPES = {
    'university_a': 'dot',
    'university_b': 'diamond'
};

async function initGraph() {
    if (isGraphLoaded) return;
    try {
        const data = await API.graphFull();
        graphData = data;
        renderGraph(data);
        renderLegend();
        isGraphLoaded = true;
    } catch (e) {
        console.error('加载图谱失败:', e);
    }
}

function renderGraph(data) {
    const container = document.getElementById('graphContainer');
    if (!container) return;

    const visNodes = data.nodes.map(n => {
        const colors = ENTITY_COLORS[n.entity] || { background: '#888', border: '#666' };
        return {
            id: n.id,
            label: n.label,
            title: n.title,
            group: n.group,
            color: {
                background: colors.background, border: colors.border,
                highlight: { background: '#fff', border: colors.background }
            },
            shape: SOURCE_SHAPES[n.source] || 'dot',
            size: 10,
            font: { color: '#a09cb5', size: 9 }
        };
    });

    const visEdges = data.edges.map((e, i) => ({
        id: i,
        from: e.from,
        to: e.to,
        label: e.label,
        color: { color: 'rgba(120,100,255,0.2)', highlight: 'rgba(120,100,255,0.6)' },
        font: { color: '#6b6785', size: 8 },
        arrows: { to: { enabled: true, scaleFactor: 0.5 } },
        smooth: { type: 'curvedCW', roundness: 0.2 }
    }));

    const options = {
        physics: {
            forceAtlas2Based: { gravitationalConstant: -26, centralGravity: 0.005, springLength: 100 },
            solver: 'forceAtlas2Based',
            stabilization: { iterations: 50 }
        },
        interaction: {
            hover: true,
            tooltipDelay: 200,
            zoomView: true,
            dragView: true
        },
        nodes: { borderWidth: 2, shadow: true },
        edges: { width: 1 }
    };

    graphNetwork = new vis.Network(container, { nodes: visNodes, edges: visEdges }, options);
}

function renderLegend() {
    const legend = document.getElementById('graphLegend');
    if (!legend) return;
    let html = '';
    for (const [entity, colors] of Object.entries(ENTITY_COLORS)) {
        html += `<div class="legend-item"><span class="legend-dot" style="background:${colors.background}"></span>${entity}</div>`;
    }
    html += '<div class="legend-item"><span style="font-size:10px">●</span> A大学</div>';
    html += '<div class="legend-item"><span style="font-size:10px">◆</span> B大学</div>';
    legend.innerHTML = html;
}

async function highlightGraphNodes(entities, conditions) {
    if (!graphNetwork || !isGraphLoaded) return;
    try {
        const result = await API.graphHighlight(entities, conditions || []);
        const ids = new Set(result.highlight_ids);

        // 高亮节点
        const allNodeIds = graphData.nodes.map(n => n.id);
        const updates = allNodeIds.map(id => {
            const node = graphData.nodes.find(n => n.id === id);
            const colors = ENTITY_COLORS[node.entity] || { background: '#888', border: '#666' };
            if (ids.has(id)) {
                return {
                    id, size: 20, font: { size: 12, color: '#fff' },
                    color: { background: colors.background, border: '#fff' },
                    shadow: { enabled: true, color: colors.background, size: 15 }
                };
            } else {
                return {
                    id, size: 6, font: { size: 7, color: '#555' },
                    color: { background: colors.background + '44', border: colors.border + '44' },
                    shadow: false
                };
            }
        });

        // 重新渲染更新
        if (graphNetwork) {
            graphNetwork.body.data.nodes.update(updates);
            // 聚焦到高亮节点
            if (ids.size > 0) {
                graphNetwork.fit({ nodes: [...ids], animation: { duration: 800, easingFunction: 'easeInOutQuad' } });
            }
        }

        // 缩小图谱面板，给聊天更多空间
        const panel = document.getElementById('graphPanel');
        if (panel) panel.classList.remove('expanded');
    } catch (e) {
        console.error('高亮图谱失败:', e);
    }
}

function resetGraphZoom() {
    if (graphNetwork) {
        // 恢复所有节点
        const updates = graphData.nodes.map(n => {
            const colors = ENTITY_COLORS[n.entity] || { background: '#888', border: '#666' };
            return {
                id: n.id, size: 10, font: { size: 9, color: '#a09cb5' },
                color: { background: colors.background, border: colors.border },
                shadow: true
            };
        });
        graphNetwork.body.data.nodes.update(updates);
        graphNetwork.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
    }
}

function toggleGraphPanel() {
    const panel = document.getElementById('graphPanel');
    panel.classList.toggle('collapsed');
}
