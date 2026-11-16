const tg = window.Telegram?.WebApp;
const telegramInitData = tg?.initData || "";
let currentProfile = null;
if (tg) { tg.ready(); tg.expand(); }

const categoryNames={web:"🌐 Сайты и лендинги",telegram:"🤖 Telegram-боты",ai:"🧠 ИИ и автоматизация",python:"🐍 Python и API"};
const PAGE_LIMIT=20, FAVORITES_KEY="vortex:favorites:v1", FAVORITE_DATA_KEY="vortex:favorite-orders:v1";
let orders=[], selectedCategory="", searchTimer=null, loading=false, loadError="", favorites=new Set(), favoriteOrders=new Map(), favoritesOnly=false;
let pageInfo={total:0,nextOffset:0,hasMore:false}, activeOrder=null;
function saveFavorites(){updateFavoriteCount()}
async function apiFetch(url, options={}){
 const headers={Accept:"application/json", ...(options.headers||{})};
 if(telegramInitData) headers["X-Telegram-Init-Data"]=telegramInitData;
 if(options.body) headers["Content-Type"]="application/json";
 const response=await fetch(url,{...options,headers});
 if(!response.ok){let message="Ошибка запроса";try{message=(await response.json()).detail||message}catch{}throw new Error(message)}
 return response.json();
}
async function loadAccount(){
 if(!telegramInitData){document.getElementById("profile-name").textContent="Откройте Vortex в Telegram";document.getElementById("profile-status").textContent="Требуется вход через Telegram";document.getElementById("profile-note").textContent="Для регистрации и синхронизации избранного откройте Mini App из Telegram. Обычный браузер не передаёт проверяемые данные аккаунта.";return}
 try{
  currentProfile=await apiFetch("/api/me");
  const name=[currentProfile.first_name,currentProfile.last_name].filter(Boolean).join(" ")||"Пользователь Telegram";
  document.getElementById("profile-name").textContent=name;
  document.getElementById("profile-username").textContent=currentProfile.username?"@"+currentProfile.username:"Имя пользователя не указано";
  document.getElementById("profile-id").textContent=String(currentProfile.telegram_id);
  document.getElementById("profile-status").textContent="Авторизован · профиль сохранён";
  document.getElementById("profile-avatar").textContent=(currentProfile.first_name||"V").slice(0,1).toUpperCase();
  document.getElementById("avatar").textContent=(currentProfile.first_name||"V").slice(0,1).toUpperCase();
  document.getElementById("profile-specialization").value=currentProfile.specialization||"";
  document.getElementById("profile-skills").value=currentProfile.skills||"";
  document.getElementById("profile-budget-min").value=currentProfile.budget_min??"";
  document.getElementById("profile-budget-max").value=currentProfile.budget_max??"";
  document.querySelectorAll('[name="profile-category"]').forEach(c=>c.checked=(currentProfile.categories||[]).includes(c.value));
  document.getElementById("profile-form").hidden=false;
  await loadFavorites();
 }catch(error){document.getElementById("profile-status").textContent="Не удалось авторизоваться";document.getElementById("profile-note").textContent=error.message}
}
async function loadFavorites(){
 const data=await apiFetch("/api/favorites");favoriteOrders=new Map((data.items||[]).map(o=>[String(o.id),o]));favorites=new Set(favoriteOrders.keys());updateFavoriteCount();renderOrders();
}

function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function safeUrl(v){try{const u=new URL(v);return ["http:","https:"].includes(u.protocol)?u.href:""}catch{return ""}}
function categoryLabel(c){return categoryNames[c]||"Другая задача"}
function updateFavoriteCount(){document.getElementById("favorite-count").textContent=favorites.size;document.getElementById("nav-favorite-count").textContent=favorites.size;document.getElementById("profile-favorites").textContent=favorites.size}
function publishedTimestamp(label){
 const months={"января":0,"февраля":1,"марта":2,"апреля":3,"мая":4,"июня":5,"июля":6,"августа":7,"сентября":8,"октября":9,"ноября":10,"декабря":11};
 const m=String(label||"").match(/^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$/i);
 return m&&months[m[2].toLowerCase()]!==undefined?new Date(Number(m[3]),months[m[2].toLowerCase()],Number(m[1])).getTime():0;
}
function visibleOrders(){
 const q=document.getElementById("search").value.trim().toLocaleLowerCase("ru");
 const source=favoritesOnly?[...favoriteOrders.values()]:orders;
 return source.filter(o=>(!selectedCategory||o.category===selectedCategory)&&(!favoritesOnly||favorites.has(String(o.id)))&&[o.title,o.source,o.price,o.published_label,categoryLabel(o.category)].join(" ").toLocaleLowerCase("ru").includes(q))
 .sort((a,b)=>{const s=document.getElementById("sort").value;if(s==="title")return (a.title||"").localeCompare(b.title||"","ru");if(s==="old")return publishedTimestamp(a.published_label)-publishedTimestamp(b.published_label);return publishedTimestamp(b.published_label)-publishedTimestamp(a.published_label)});
}
function updateStatus(shown){
 document.getElementById("count").textContent=String(shown);
 const pill=document.getElementById("catalog-pill"), notice=document.getElementById("catalog-notice");
 pill.textContent=loading&&orders.length===0?"ЗАГРУЗКА":loadError&&orders.length===0?"НЕТ СВЯЗИ":pageInfo.total===0?"ПУСТО":"КАТАЛОГ";
 notice.textContent=loadError?"Не удалось обновить каталог. Проверьте соединение и повторите попытку.":pageInfo.total===0?"В каталоге пока нет заказов.":`Загружено ${orders.length} из ${pageInfo.total}. Поиск и фильтры применяются к загруженным заказам.`;
 document.getElementById("catalog-actions").hidden=!pageInfo.hasMore;
 document.getElementById("loadMore").disabled=loading;
 document.getElementById("loadMore").textContent=loading?"Загружаем…":"Показать ещё";
}
function renderOrders(){
 const el=document.getElementById("orders"), list=visibleOrders();updateStatus(list.length);
 if(loading&&orders.length===0){el.innerHTML='<div class="loading">Загружаем заказы…</div>';return}
 if(loadError&&orders.length===0){el.innerHTML='<div class="error">Не удалось загрузить заказы. <button id="retryCatalog" class="text-button">Повторить</button></div>';document.getElementById("retryCatalog").onclick=()=>loadCatalog();return}
 if(!list.length){el.innerHTML=`<div class="empty">${favoritesOnly?"В избранном пока пусто.":"Ничего не найдено. Измените фильтры или поисковый запрос."}</div>`;return}
 el.innerHTML=list.map(o=>`<article class="order-card" data-id="${escapeHtml(o.id)}" tabindex="0" role="button" aria-label="Подробнее: ${escapeHtml(o.title)}">
 <div class="card-top"><span class="order-category">${escapeHtml(categoryLabel(o.category))}</span><button class="favorite-button ${favorites.has(String(o.id))?"saved":""}" data-favorite="${escapeHtml(o.id)}" aria-label="Добавить в избранное">${favorites.has(String(o.id))?"♥":"♡"}</button></div>
 <h3>${escapeHtml(o.title||"Заказ без описания")}</h3><p>${escapeHtml([o.source||"Источник не указан",o.published_label].filter(Boolean).join(" · "))}</p>
 <div class="order-bottom"><span class="order-price">${escapeHtml(o.price||"Бюджет не указан")}</span><span class="details-hint">Подробнее →</span></div></article>`).join("");
 el.querySelectorAll(".order-card").forEach(card=>{const open=()=>showDetails(orders.find(o=>String(o.id)===card.dataset.id)||favoriteOrders.get(card.dataset.id));card.addEventListener("click",e=>{if(!e.target.closest("[data-favorite]"))open()});card.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open()}})});
 el.querySelectorAll("[data-favorite]").forEach(btn=>btn.addEventListener("click",e=>{e.stopPropagation();toggleFavorite(btn.dataset.favorite)}));
}
async function toggleFavorite(id){id=String(id);const item=orders.find(o=>String(o.id)===id)||favoriteOrders.get(id);try{if(favorites.has(id)){await apiFetch(`/api/favorites/${encodeURIComponent(id)}`,{method:"DELETE"});favorites.delete(id);favoriteOrders.delete(id)}else{if(!telegramInitData)throw new Error("Откройте приложение через Telegram для сохранения избранного");if(!item)throw new Error("Не удалось получить карточку заказа");await apiFetch("/api/favorites",{method:"POST",body:JSON.stringify({order:item})});favorites.add(id);favoriteOrders.set(id,item)}saveFavorites();renderOrders();if(activeOrder&&String(activeOrder.id)===id)updateDetailFavorite()}catch(error){alert(error.message)}}
function updateDetailFavorite(){const b=document.getElementById("detail-favorite");if(activeOrder){const saved=favorites.has(String(activeOrder.id));b.textContent=saved?"♥ В избранном":"♡ В избранное"}}
function showDetails(order){if(!order)return;activeOrder=order;document.getElementById("detail-category").textContent=categoryLabel(order.category);document.getElementById("detail-title").textContent=order.title||"Заказ без описания";document.getElementById("detail-meta").textContent=[order.source||"Источник не указан",order.published_label].filter(Boolean).join(" · ");document.getElementById("detail-price").textContent=order.price||"Бюджет не указан";document.getElementById("detail-description").textContent="Подробное описание доступно в оригинальной публикации. Откройте источник, чтобы посмотреть условия и связаться с заказчиком.";const link=document.getElementById("detail-source"),url=safeUrl(order.url);link.href=url;link.hidden=!url;document.getElementById("detail-modal").hidden=false;updateDetailFavorite()}
function closeDetails(){document.getElementById("detail-modal").hidden=true;activeOrder=null}
function selectCategory(c){selectedCategory=c;document.querySelectorAll(".category").forEach(b=>b.classList.toggle("active",b.dataset.category===c));renderOrders()}
async function loadCatalog({append=false}={}){
 if(loading)return;loading=true;loadError="";renderOrders();const offset=append?pageInfo.nextOffset:0;
 try{const r=await fetch(`/api/orders?limit=${PAGE_LIMIT}&offset=${offset}`,{headers:{Accept:"application/json"}});if(!r.ok)throw Error("catalog");const p=await r.json();const items=Array.isArray(p.items)?p.items:[];const known=new Set(orders.map(o=>String(o.id)));orders=append?orders.concat(items.filter(o=>!known.has(String(o.id)))):items;pageInfo={total:Number(p.total)||0,nextOffset:Number(p.next_offset)||offset+items.length,hasMore:Boolean(p.has_more)}}
 catch(e){loadError="unavailable";if(!append){orders=[];pageInfo={total:0,nextOffset:0,hasMore:false}}}finally{loading=false;renderOrders()}
}
document.addEventListener("DOMContentLoaded",()=>{
 document.querySelectorAll(".category").forEach(b=>b.addEventListener("click",()=>selectCategory(b.dataset.category||"")));
 document.getElementById("clearCategory").onclick=()=>{selectCategory("");document.getElementById("search").value="";favoritesOnly=false;syncFavoritesButton();renderOrders()};
 document.getElementById("search").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(renderOrders,120)});
 document.getElementById("sort").addEventListener("change",renderOrders);
 document.getElementById("favorites-toggle").onclick=()=>{favoritesOnly=!favoritesOnly;syncFavoritesButton();renderOrders()};
 document.querySelectorAll(".nav-tab").forEach(tab=>tab.addEventListener("click",()=>switchView(tab.dataset.view)));
 document.getElementById("loadMore").onclick=()=>loadCatalog({append:true});
 document.getElementById("close-detail").onclick=closeDetails;
 document.getElementById("detail-modal").addEventListener("click",e=>{if(e.target.id==="detail-modal")closeDetails()});
 document.getElementById("detail-favorite").onclick=()=>activeOrder&&toggleFavorite(activeOrder.id);
 document.addEventListener("keydown",e=>{if(e.key==="Escape")closeDetails()});
 updateFavoriteCount();loadCatalog();loadAccount();
 document.getElementById("profile-form").addEventListener("submit",async e=>{
  e.preventDefault();const status=document.getElementById("profile-save-status");status.textContent="Сохраняем…";
  const min=document.getElementById("profile-budget-min").value, max=document.getElementById("profile-budget-max").value;
  const payload={specialization:document.getElementById("profile-specialization").value,skills:document.getElementById("profile-skills").value,categories:[...document.querySelectorAll('[name="profile-category"]:checked')].map(x=>x.value),budget_min:min===""?null:Number(min),budget_max:max===""?null:Number(max)};
  try{currentProfile=await apiFetch("/api/me",{method:"PUT",body:JSON.stringify(payload)});status.textContent="Профиль успешно сохранён"}catch(error){status.textContent=error.message}
 });
});
function switchView(view){
 const ordersView=document.getElementById("orders-view"),profileView=document.getElementById("profile-view");
 ordersView.hidden=view==="profile";profileView.hidden=view!=="profile";
 favoritesOnly=view==="favorites";syncFavoritesButton();
 document.querySelectorAll(".nav-tab").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
 if(view!=="profile"){document.getElementById("list-title").textContent=view==="favorites"?"Избранные заказы":"Лента заказов";renderOrders();}
 window.scrollTo({top:0,behavior:"smooth"});
}
function syncFavoritesButton(){const b=document.getElementById("favorites-toggle");b.classList.toggle("active",favoritesOnly);b.setAttribute("aria-pressed",String(favoritesOnly));document.getElementById("list-title").textContent=favoritesOnly?"Избранные заказы":"Лента заказов"}
