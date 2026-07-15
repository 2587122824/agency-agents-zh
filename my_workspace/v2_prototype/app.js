const jobs = [
  { id: 'SH-002', name: '低头系紧跑鞋', route: 'identity_scene_keyframe', state: 'done', image: 'https://images.unsplash.com/photo-1539185441755-769473a23570?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-003', name: '跑道边动态热身', route: 'identity_scene_keyframe', state: 'done', image: 'https://images.unsplash.com/photo-1486218119243-13883505764c?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-004', name: '起跑前深呼吸', route: 'identity_scene_keyframe', state: 'done', image: 'https://images.unsplash.com/photo-1594381898411-846e7d193883?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-006', name: '弯道持续加速', route: 'identity_scene_keyframe', state: 'done', image: 'https://images.unsplash.com/photo-1552674605-db6ffd4facb5?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-009', name: '负重深蹲', route: 'identity_scene_keyframe', state: 'running', image: 'https://images.unsplash.com/photo-1581009146145-b5ef050c2e1e?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-010', name: '弹力带侧步', route: 'identity_scene_keyframe', state: 'waiting', image: 'https://images.unsplash.com/photo-1534438327276-14e5300c3a48?auto=format&fit=crop&w=160&q=75' },
  { id: 'SH-012', name: '蹲踞式起跑', route: 'identity_scene_keyframe', state: 'waiting', image: 'https://images.unsplash.com/photo-1571008887538-b36bb32f4571?auto=format&fit=crop&w=160&q=75' },
];

const assets = [
  { id:'SH-003', title:'跑道边动态热身', status:'review', note:'动作镜头 · 4.0s', issue:'人物相似度建议人工确认', image:'https://images.unsplash.com/photo-1486218119243-13883505764c?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-004', title:'起跑前深呼吸', status:'review', note:'人物近景 · 2.5s', issue:'正脸身份建议人工确认', image:'https://images.unsplash.com/photo-1594381898411-846e7d193883?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-006', title:'弯道持续加速', status:'passed', note:'动作镜头 · 4.0s', issue:'运动幅度符合合同', image:'https://images.unsplash.com/photo-1552674605-db6ffd4facb5?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-008', title:'力量区准备', status:'passed', note:'人物中景 · 3.0s', issue:'构图与场景锚点一致', image:'https://images.unsplash.com/photo-1581009146145-b5ef050c2e1e?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-009', title:'负重深蹲', status:'waiting', note:'人物全身 · 4.0s', issue:'正在生产关键帧', image:'https://images.unsplash.com/photo-1534438327276-14e5300c3a48?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-012', title:'蹲踞式起跑', status:'review', note:'人物全身 · 3.5s', issue:'起跑姿态建议人工确认', image:'https://images.unsplash.com/photo-1571008887538-b36bb32f4571?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-014', title:'终点前冲刺', status:'passed', note:'动作镜头 · 4.0s', issue:'运动幅度符合合同', image:'https://images.unsplash.com/photo-1526676037777-05a232554f77?auto=format&fit=crop&w=500&q=82' },
  { id:'SH-015', title:'夕阳下收尾', status:'waiting', note:'人物背影 · 3.0s', issue:'等待上一阶段完成', image:'https://images.unsplash.com/photo-1502904550040-7534597429ae?auto=format&fit=crop&w=500&q=82' },
];

const stateLabels = { done:'已完成', running:'生成中', waiting:'等待中' };
const qcLabels = { review:'待审核', passed:'已通过', waiting:'生产中' };
let currentScreen = 'overview';
let impactMode = 'outfit';

function renderJobs() {
  document.querySelector('#jobList').innerHTML = jobs.map(job => `
    <div class="job-row">
      <span class="job-preview" style="background-image:url('${job.image}')"></span>
      <div class="job-name"><b>${job.id} · ${job.name}</b><small>人物参考 + 场景基准 · outfit_training_01</small></div>
      <div class="job-route"><small>实际路由</small><b>${job.route}</b>${job.state === 'running' ? '<div class="running-bar"><span></span></div>' : ''}</div>
      <span class="job-state ${job.state}">${stateLabels[job.state]}</span>
      <button class="icon-button" title="查看任务详情"><i data-lucide="more-horizontal"></i></button>
    </div>`).join('');
}

function renderAssets() {
  document.querySelector('#assetGrid').innerHTML = assets.map(asset => `
    <article class="asset-card" data-status="${asset.status}" data-id="${asset.id}">
      <div class="asset-media" style="background-image:url('${asset.image}')"><span class="asset-check"><i data-lucide="check"></i></span><span class="asset-duration">${asset.note.split('·')[1]?.trim() || ''}</span></div>
      <div class="asset-info"><div class="asset-title"><b>${asset.id} · ${asset.title}</b><span class="qc-pill ${asset.status}">${qcLabels[asset.status]}</span></div><p>${asset.note}</p><div class="issue-row"><i data-lucide="${asset.status === 'passed' ? 'badge-check' : asset.status === 'waiting' ? 'clock-3' : 'circle-alert'}"></i>${asset.issue}</div></div>
    </article>`).join('');
  document.querySelectorAll('.asset-card').forEach(card => card.addEventListener('click', () => card.classList.toggle('selected')));
}

function showToast(message) {
  const toast = document.querySelector('#toast');
  toast.querySelector('span').textContent = message;
  toast.classList.add('show');
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
}

function switchScreen(name) {
  const normalized = ['overview','brief','plan','production','review','timeline','assets','settings'].includes(name) ? name : 'overview';
  currentScreen = normalized;
  document.querySelectorAll('.screen').forEach(screen => screen.classList.toggle('active', screen.id === `screen-${normalized}`));
  const stepMap = { create:'brief', brief:'brief', plan:'plan', production:'production', review:'review', timeline:'review' };
  document.querySelectorAll('.step').forEach(step => step.classList.toggle('active', step.dataset.step === (stepMap[normalized] || '')));
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === (normalized === 'brief' || normalized === 'plan' ? 'create' : normalized)));
  document.querySelector('.workspace').classList.toggle('dashboard-mode', normalized === 'overview');
  updatePrimaryAction(normalized);
  document.querySelector('.sidebar').classList.remove('open');
}

function updatePrimaryAction(screen) {
  const button = document.querySelector('#continueButton');
  const labels = {
    overview: '处理待审核素材',
    brief: '检查创作方案',
    plan: '确认方案并创建快照',
    production: '查看素材审核',
    review: '进入剪辑台',
    timeline: '检查交付条件',
    assets: '返回项目控制台',
    settings: '返回项目控制台',
  };
  button.childNodes[0].textContent = labels[screen] || '返回项目控制台';
}

document.querySelectorAll('.step').forEach(step => step.addEventListener('click', () => switchScreen(step.dataset.step)));
document.querySelectorAll('.nav-item').forEach(item => item.addEventListener('click', () => switchScreen(item.dataset.view)));
document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click', () => switchScreen(button.dataset.goto)));
document.querySelector('.mobile-menu').addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));
document.querySelector('.project-row.selected').addEventListener('click', () => switchScreen('overview'));

document.querySelectorAll('.visual-option').forEach(option => option.addEventListener('click', () => {
  option.parentElement.querySelectorAll('.visual-option').forEach(item => item.classList.remove('selected'));
  option.classList.add('selected');
  showToast(`画面质感已选择：${option.dataset.value}`);
}));

document.querySelectorAll('.segmented').forEach(group => group.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
  group.querySelectorAll('button').forEach(item => item.classList.remove('selected'));
  button.classList.add('selected');
})));

document.querySelectorAll('.quick-prompts button').forEach(button => button.addEventListener('click', () => {
  document.querySelector('#chatInput').value = button.textContent;
  document.querySelector('#chatInput').focus();
}));

document.querySelector('#sendMessage').addEventListener('click', () => {
  const input = document.querySelector('#chatInput');
  if (!input.value.trim()) return showToast('先写下你想调整的内容');
  showToast('原型已记录这条创作意见');
  input.value = '';
});

document.querySelector('#continueButton').addEventListener('click', () => {
  const targets = { overview:'review', brief:'plan', production:'review', review:'timeline', assets:'overview', settings:'overview' };
  if (currentScreen === 'plan') return openImpact('snapshot');
  if (currentScreen === 'timeline') return showToast('仍有 1 个时间线空位，暂不满足交付条件');
  switchScreen(targets[currentScreen] || 'overview');
});

document.querySelector('#startStage').addEventListener('click', () => showToast('原型演示：需要二次确认后才会提交生产'));
document.querySelector('#approveSelected').addEventListener('click', () => {
  const selected = [...document.querySelectorAll('.asset-card.selected')];
  if (!selected.length) return showToast('请先选择要通过的素材');
  selected.forEach(card => {
    card.dataset.status = 'passed';
    card.classList.remove('selected');
    card.querySelector('.qc-pill').className = 'qc-pill passed';
    card.querySelector('.qc-pill').textContent = '已通过';
    card.querySelector('.issue-row').innerHTML = '<i data-lucide="badge-check"></i>已由你确认通过';
  });
  lucide.createIcons();
  showToast(`已通过 ${selected.length} 个素材，仅更新审核状态`);
});

function setImpactValue(id, value) {
  document.querySelector(id).textContent = value;
}

function openImpact(mode) {
  impactMode = mode;
  const dialog = document.querySelector('#impactDialog');
  const items = document.querySelector('#impactItems');
  if (mode === 'retry') {
    const selected = [...document.querySelectorAll('.asset-card.selected')];
    if (!selected.length) return showToast('请先选择要重做的素材');
    const count = selected.length;
    document.querySelector('#impactTitle').textContent = `重做已选 ${count} 个素材`;
    document.querySelector('#impactDescription').textContent = '只会为选中的素材创建新尝试，并重新评估它们的真实下游依赖。';
    setImpactValue('#impactShots', String(count));
    setImpactValue('#impactAssets', String(count));
    setImpactValue('#impactCalls', String(count));
    setImpactValue('#impactCost', `¥ ${(count * 1.55).toFixed(2)}`);
    items.innerHTML = selected.map(card => `<p><i data-lucide="clapperboard"></i>${card.dataset.id} · 新工作尝试</p>`).join('') + '<p><i data-lucide="git-compare-arrows"></i>仅失效真实引用这些素材的下游结果</p>';
    document.querySelector('#impactWarning').textContent = '确认后只记录本次用户重做请求；原型不会调用供应商或扣费。';
    document.querySelector('#confirmImpact').textContent = `确认重做 ${count} 项`;
  } else if (mode === 'snapshot') {
    document.querySelector('#impactTitle').textContent = '确认方案并创建生产快照';
    document.querySelector('#impactDescription').textContent = '系统将冻结当前决策、实体版本、分镜合同、视频规格和生产配置。';
    setImpactValue('#impactShots', '15');
    setImpactValue('#impactAssets', '0');
    setImpactValue('#impactCalls', '30');
    setImpactValue('#impactCost', '¥ 24.80');
    items.innerHTML = '<p><i data-lucide="git-branch"></i>plan_v1 · 已确认方案</p><p><i data-lucide="camera"></i>snapshot_001 · 待锁定</p><p><i data-lucide="volume-x"></i>音频关闭 · 不创建 TTS 节点</p>';
    document.querySelector('#impactWarning').textContent = '创建快照不会开始付费生产，提交生产时仍需再次确认费用。';
    document.querySelector('#confirmImpact').textContent = '创建 snapshot_001';
  } else {
    document.querySelector('#impactTitle').textContent = '修改训练服状态';
    document.querySelector('#impactDescription').textContent = '这项修改会创建新方案和新生产快照，不会覆盖当前结果。';
    setImpactValue('#impactShots', '12');
    setImpactValue('#impactAssets', '20');
    setImpactValue('#impactCalls', '27');
    setImpactValue('#impactCost', '¥ 22.40');
    items.innerHTML = '<p><i data-lucide="user-round"></i>char_main 与 outfit_training_01</p><p><i data-lucide="clapperboard"></i>SH-002 至 SH-015 的人物镜头</p><p><i data-lucide="list-video"></i>当前剪辑草案需要重新验证</p>';
    document.querySelector('#impactWarning').textContent = '确认后只创建 plan_v2 草稿，不会自动生产或扣费。';
    document.querySelector('#confirmImpact').textContent = '创建 plan_v2 草稿';
  }
  lucide.createIcons();
  dialog.showModal();
}

function closeImpact() {
  document.querySelector('#impactDialog').close();
}

document.querySelectorAll('[data-open-impact]').forEach(button => button.addEventListener('click', () => openImpact(button.dataset.openImpact)));
document.querySelector('#retrySelected').addEventListener('click', () => openImpact('retry'));
document.querySelector('#closeImpact').addEventListener('click', closeImpact);
document.querySelector('#cancelImpact').addEventListener('click', closeImpact);
document.querySelector('#impactDialog').addEventListener('click', event => {
  if (event.target === event.currentTarget) closeImpact();
});
document.querySelector('#confirmImpact').addEventListener('click', () => {
  const messages = {
    retry: '已记录选中素材的重做确认，原型未发起付费调用',
    snapshot: '已确认创建 snapshot_001，原型未提交生产',
    outfit: '已创建 plan_v2 变更草稿，当前快照保持不变',
  };
  closeImpact();
  showToast(messages[impactMode]);
});

document.querySelectorAll('.filter-tabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.filter-tabs button').forEach(item => item.classList.remove('selected'));
  button.classList.add('selected');
  const filter = button.dataset.filter;
  document.querySelectorAll('.asset-card').forEach(card => card.classList.toggle('hidden', filter !== 'all' && card.dataset.status !== filter));
}));

renderJobs();
renderAssets();
lucide.createIcons();
