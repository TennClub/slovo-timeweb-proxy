"""Slovo: a small shared vocabulary bot. Run: python slovo.py"""
import asyncio
import html
import json
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

PAGE_SIZE = 8
MAX_CARDS_PER_FOLDER = 50
MAX_CARDS_PER_TOPIC = 30
ROLES = {"owner": "Владелец", "editor": "Редактор", "member": "Участник"}
ROLE_LABELS = {
    "ru":{"owner":"Владелец","editor":"Редактор","member":"Участник"},
    "en":{"owner":"Owner","editor":"Editor","member":"Member"},
    "es":{"owner":"Propietario","editor":"Editor","member":"Miembro"},
    "fr":{"owner":"Propriétaire","editor":"Éditeur","member":"Membre"},
    "zh":{"owner":"所有者","editor":"编辑者","member":"成员"},
    "ar":{"owner":"المالك","editor":"المحرر","member":"عضو"},
}
INTERVALS = (1, 3, 7, 14, 30)
MAX_ACTIVE_GAP_SECONDS = 180
LANGUAGES = {
    "en": "English", "ru": "Русский", "es": "Español",
    "fr": "Français", "zh": "中文", "ar": "العربية",
}

TEXT = {
 "ru": {"menu":"<b>Slovo</b> — твои слова под рукой.\nДобавляйте слова вместе, учите в своём темпе.","folders":"📁 Мои папки","add":"➕ Добавить слова","learn":"📚 Учить","language":"🌐 Язык","back":"Назад","cancel":"Отмена","new_folder":"➕ Создать папку","no_folders":"Папок пока нет.","folder_name":"Как назвать папку?","source_lang":"Выберите первый язык папки.","target_lang":"Выберите второй язык папки.","words":"Слов","due":"К повторению","word_list":"Список слов","invite":"Пригласить","settings":"Настройки","study":"Учить","choose_folder":"Выберите папку.","choose_study":"Выберите направление обучения.","choose_mode":"Что будем учить?","due_mode":"Повторить нужное","all_mode":"Все слова","know":"Знаю","dont_know":"Не знаю","finish":"Закончить","next":"Дальше","correct_next":"Верно, дальше","mistake":"Ошибся","lang_saved":"Язык интерфейса изменён.","no_access":"Нет доступа.","session_gone":"Эта сессия уже недоступна.","question":"Слово {pos} из {total}","done":"<b>Готово!</b>\nПройдено слов: {total}.\nБез ошибок: {ok}.\nСтоит повторить: {bad}.","repeat_errors":"Повторить ошибки","more":"Ещё 10 слов","to_folder":"В папку"},
 "en": {"menu":"<b>Slovo</b> — your words at hand.\nAdd words together and learn at your own pace.","folders":"📁 My folders","add":"➕ Add words","learn":"📚 Learn","language":"🌐 Language","back":"Back","cancel":"Cancel","new_folder":"➕ Create folder","no_folders":"No folders yet.","folder_name":"What should the folder be called?","source_lang":"Choose the first language.","target_lang":"Choose the second language.","words":"Words","due":"Due","word_list":"Word list","invite":"Invite","settings":"Settings","study":"Learn","choose_folder":"Choose a folder.","choose_study":"Choose the study direction.","choose_mode":"What would you like to study?","due_mode":"Review due","all_mode":"All words","know":"I know","dont_know":"I don't know","finish":"Finish","next":"Next","correct_next":"Correct, next","mistake":"I was wrong","lang_saved":"Interface language changed.","no_access":"Access denied.","session_gone":"This session is no longer available.","question":"Word {pos} of {total}","done":"<b>Done!</b>\nWords studied: {total}.\nWithout mistakes: {ok}.\nWorth reviewing: {bad}.","repeat_errors":"Review mistakes","more":"10 more words","to_folder":"To folder"},
 "es": {"menu":"<b>Slovo</b> — tus palabras a mano.\nAñadan palabras juntos y aprendan a su ritmo.","folders":"📁 Mis carpetas","add":"➕ Añadir palabras","learn":"📚 Aprender","language":"🌐 Idioma","back":"Atrás","cancel":"Cancelar","new_folder":"➕ Crear carpeta","no_folders":"Aún no hay carpetas.","folder_name":"¿Cómo se llamará la carpeta?","source_lang":"Elige el primer idioma.","target_lang":"Elige el segundo idioma.","words":"Palabras","due":"Para repasar","word_list":"Lista de palabras","invite":"Invitar","settings":"Ajustes","study":"Aprender","choose_folder":"Elige una carpeta.","choose_study":"Elige la dirección de estudio.","choose_mode":"¿Qué quieres estudiar?","due_mode":"Repasar pendientes","all_mode":"Todas las palabras","know":"Lo sé","dont_know":"No lo sé","finish":"Terminar","next":"Siguiente","correct_next":"Correcto, siguiente","mistake":"Me equivoqué","lang_saved":"Idioma de la interfaz cambiado.","no_access":"Acceso denegado.","session_gone":"Esta sesión ya no está disponible.","question":"Palabra {pos} de {total}","done":"<b>¡Listo!</b>\nPalabras estudiadas: {total}.\nSin errores: {ok}.\nPara repasar: {bad}.","repeat_errors":"Repetir errores","more":"10 palabras más","to_folder":"A la carpeta"},
 "fr": {"menu":"<b>Slovo</b> — vos mots à portée de main.\nAjoutez des mots ensemble et apprenez à votre rythme.","folders":"📁 Mes dossiers","add":"➕ Ajouter des mots","learn":"📚 Apprendre","language":"🌐 Langue","back":"Retour","cancel":"Annuler","new_folder":"➕ Créer un dossier","no_folders":"Aucun dossier pour le moment.","folder_name":"Comment nommer le dossier ?","source_lang":"Choisissez la première langue.","target_lang":"Choisissez la deuxième langue.","words":"Mots","due":"À réviser","word_list":"Liste des mots","invite":"Inviter","settings":"Paramètres","study":"Apprendre","choose_folder":"Choisissez un dossier.","choose_study":"Choisissez le sens d’apprentissage.","choose_mode":"Que voulez-vous apprendre ?","due_mode":"Réviser maintenant","all_mode":"Tous les mots","know":"Je sais","dont_know":"Je ne sais pas","finish":"Terminer","next":"Suivant","correct_next":"Correct, suivant","mistake":"Je me suis trompé","lang_saved":"Langue de l’interface modifiée.","no_access":"Accès refusé.","session_gone":"Cette session n’est plus disponible.","question":"Mot {pos} sur {total}","done":"<b>Terminé !</b>\nMots étudiés : {total}.\nSans erreur : {ok}.\nÀ réviser : {bad}.","repeat_errors":"Revoir les erreurs","more":"10 mots de plus","to_folder":"Au dossier"},
 "zh": {"menu":"<b>Slovo</b> — 随身单词本。\n一起添加单词，按自己的节奏学习。","folders":"📁 我的文件夹","add":"➕ 添加单词","learn":"📚 学习","language":"🌐 语言","back":"返回","cancel":"取消","new_folder":"➕ 新建文件夹","no_folders":"暂无文件夹。","folder_name":"文件夹叫什么名字？","source_lang":"选择第一种语言。","target_lang":"选择第二种语言。","words":"单词数","due":"待复习","word_list":"单词列表","invite":"邀请","settings":"设置","study":"学习","choose_folder":"选择文件夹。","choose_study":"选择学习方向。","choose_mode":"要学习什么？","due_mode":"复习到期单词","all_mode":"全部单词","know":"认识","dont_know":"不认识","finish":"结束","next":"下一个","correct_next":"正确，下一个","mistake":"答错了","lang_saved":"界面语言已更改。","no_access":"没有访问权限。","session_gone":"此学习会话已失效。","question":"第 {pos}/{total} 个单词","done":"<b>完成！</b>\n已学习：{total}。\n无错误：{ok}。\n建议复习：{bad}。","repeat_errors":"复习错词","more":"再学10个","to_folder":"返回文件夹"},
 "ar": {"menu":"<b>Slovo</b> — كلماتك في متناول يدك.\nأضيفوا الكلمات معًا وتعلموا بالوتيرة المناسبة.","folders":"📁 مجلداتي","add":"➕ إضافة كلمات","learn":"📚 تعلّم","language":"🌐 اللغة","back":"رجوع","cancel":"إلغاء","new_folder":"➕ إنشاء مجلد","no_folders":"لا توجد مجلدات بعد.","folder_name":"ما اسم المجلد؟","source_lang":"اختر اللغة الأولى.","target_lang":"اختر اللغة الثانية.","words":"الكلمات","due":"للمراجعة","word_list":"قائمة الكلمات","invite":"دعوة","settings":"الإعدادات","study":"تعلّم","choose_folder":"اختر مجلدًا.","choose_study":"اختر اتجاه التعلّم.","choose_mode":"ماذا تريد أن تتعلم؟","due_mode":"مراجعة المستحق","all_mode":"كل الكلمات","know":"أعرف","dont_know":"لا أعرف","finish":"إنهاء","next":"التالي","correct_next":"صحيح، التالي","mistake":"أخطأت","lang_saved":"تم تغيير لغة الواجهة.","no_access":"لا يوجد وصول.","session_gone":"هذه الجلسة لم تعد متاحة.","question":"الكلمة {pos} من {total}","done":"<b>تم!</b>\nالكلمات المدروسة: {total}.\nدون أخطاء: {ok}.\nتحتاج مراجعة: {bad}.","repeat_errors":"مراجعة الأخطاء","more":"10 كلمات أخرى","to_folder":"إلى المجلد"},
}

# Secondary screens use the same catalogue, so changing the interface language
# applies to the whole bot rather than only to the main menu.
_EXTRA = {
"ru":{"profile":"👤 Профиль","profile_title":"👤 <b>Профиль</b>","interface_lang":"Язык интерфейса","folders_count":"Папок","words_count":"Доступно слов","learned_count":"Изучено слов","sessions_count":"Тренировок","change_language":"🌐 Изменить язык","folder_unavailable":"Папка недоступна.","folder_settings":"Настройки папки","folder_languages":"Языки папки","rename":"Переименовать","members":"Участники","delete_folder":"Удалить папку","new_name":"Новое название?","send_words":"Пришлите слово с переводом или список.\nНапример: <i>apple — яблоко</i>","saved":"Сохранено.","added":"Добавлено слов: {count}.","nothing_due":"На сегодня всё. Можно повторить все слова или добавить новые.","choose_interface":"Выберите язык интерфейса.","created":"Папка создана.","empty_name":"Название не должно быть пустым.","no_cards":"В этой папке пока нет слов.","direction":"Направление","languages_saved":"Языки папки изменены.","edit_source":"Выберите первый язык папки.","edit_target":"Выберите второй язык папки.","studied":"Изучено"},
"en":{"profile":"👤 Profile","profile_title":"👤 <b>Profile</b>","interface_lang":"Interface language","folders_count":"Folders","words_count":"Available words","learned_count":"Learned words","sessions_count":"Study sessions","change_language":"🌐 Change language","folder_unavailable":"Folder unavailable.","folder_settings":"Folder settings","folder_languages":"Folder languages","rename":"Rename","members":"Members","delete_folder":"Delete folder","new_name":"New name?","send_words":"Send a word with its translation or a list.\nExample: <i>apple — яблоко</i>","saved":"Saved.","added":"Words added: {count}.","nothing_due":"Nothing is due today. You can study all words or add new ones.","choose_interface":"Choose the interface language.","created":"Folder created.","empty_name":"The name cannot be empty.","no_cards":"There are no words in this folder yet.","direction":"Direction","languages_saved":"Folder languages changed.","edit_source":"Choose the first folder language.","edit_target":"Choose the second folder language.","studied":"Learned"},
"es":{"profile":"👤 Perfil","profile_title":"👤 <b>Perfil</b>","interface_lang":"Idioma de la interfaz","folders_count":"Carpetas","words_count":"Palabras disponibles","learned_count":"Palabras aprendidas","sessions_count":"Sesiones","change_language":"🌐 Cambiar idioma","folder_unavailable":"Carpeta no disponible.","folder_settings":"Ajustes de carpeta","folder_languages":"Idiomas de la carpeta","rename":"Renombrar","members":"Miembros","delete_folder":"Eliminar carpeta","new_name":"¿Nuevo nombre?","send_words":"Envía una palabra con su traducción o una lista.\nEjemplo: <i>apple — manzana</i>","saved":"Guardado.","added":"Palabras añadidas: {count}.","nothing_due":"No hay nada pendiente hoy. Puedes estudiar todas las palabras o añadir nuevas.","choose_interface":"Elige el idioma de la interfaz.","created":"Carpeta creada.","empty_name":"El nombre no puede estar vacío.","no_cards":"Esta carpeta todavía no tiene palabras.","direction":"Dirección","languages_saved":"Idiomas de la carpeta cambiados.","edit_source":"Elige el primer idioma de la carpeta.","edit_target":"Elige el segundo idioma de la carpeta.","studied":"Aprendidas"},
"fr":{"profile":"👤 Profil","profile_title":"👤 <b>Profil</b>","interface_lang":"Langue de l’interface","folders_count":"Dossiers","words_count":"Mots disponibles","learned_count":"Mots appris","sessions_count":"Sessions","change_language":"🌐 Changer de langue","folder_unavailable":"Dossier indisponible.","folder_settings":"Paramètres du dossier","folder_languages":"Langues du dossier","rename":"Renommer","members":"Membres","delete_folder":"Supprimer le dossier","new_name":"Nouveau nom ?","send_words":"Envoyez un mot avec sa traduction ou une liste.\nExemple : <i>apple — pomme</i>","saved":"Enregistré.","added":"Mots ajoutés : {count}.","nothing_due":"Rien à réviser aujourd’hui. Vous pouvez étudier tous les mots ou en ajouter.","choose_interface":"Choisissez la langue de l’interface.","created":"Dossier créé.","empty_name":"Le nom ne peut pas être vide.","no_cards":"Ce dossier ne contient pas encore de mots.","direction":"Sens","languages_saved":"Langues du dossier modifiées.","edit_source":"Choisissez la première langue du dossier.","edit_target":"Choisissez la deuxième langue du dossier.","studied":"Appris"},
"zh":{"profile":"👤 个人资料","profile_title":"👤 <b>个人资料</b>","interface_lang":"界面语言","folders_count":"文件夹","words_count":"可用单词","learned_count":"已学单词","sessions_count":"学习次数","change_language":"🌐 更改语言","folder_unavailable":"无法访问文件夹。","folder_settings":"文件夹设置","folder_languages":"文件夹语言","rename":"重命名","members":"成员","delete_folder":"删除文件夹","new_name":"新名称？","send_words":"发送单词和翻译，或发送列表。\n例如：<i>apple — 苹果</i>","saved":"已保存。","added":"已添加 {count} 个单词。","nothing_due":"今天没有待复习内容。可以学习全部单词或添加新单词。","choose_interface":"选择界面语言。","created":"文件夹已创建。","empty_name":"名称不能为空。","no_cards":"此文件夹暂无单词。","direction":"方向","languages_saved":"文件夹语言已更改。","edit_source":"选择文件夹的第一种语言。","edit_target":"选择文件夹的第二种语言。","studied":"已学习"},
"ar":{"profile":"👤 الملف الشخصي","profile_title":"👤 <b>الملف الشخصي</b>","interface_lang":"لغة الواجهة","folders_count":"المجلدات","words_count":"الكلمات المتاحة","learned_count":"الكلمات المتعلمة","sessions_count":"جلسات التعلم","change_language":"🌐 تغيير اللغة","folder_unavailable":"المجلد غير متاح.","folder_settings":"إعدادات المجلد","folder_languages":"لغات المجلد","rename":"إعادة التسمية","members":"الأعضاء","delete_folder":"حذف المجلد","new_name":"الاسم الجديد؟","send_words":"أرسل كلمة مع ترجمتها أو قائمة.\nمثال: <i>apple — تفاحة</i>","saved":"تم الحفظ.","added":"تمت إضافة {count} كلمة.","nothing_due":"لا شيء للمراجعة اليوم. يمكنك تعلم كل الكلمات أو إضافة كلمات جديدة.","choose_interface":"اختر لغة الواجهة.","created":"تم إنشاء المجلد.","empty_name":"لا يمكن أن يكون الاسم فارغًا.","no_cards":"لا توجد كلمات في هذا المجلد بعد.","direction":"الاتجاه","languages_saved":"تم تغيير لغات المجلد.","edit_source":"اختر اللغة الأولى للمجلد.","edit_target":"اختر اللغة الثانية للمجلد.","studied":"تم تعلمها"}}
for _locale, _values in _EXTRA.items():
    TEXT[_locale].update(_values)
_MORE = {
"ru":{"joined":"Вы присоединились к папке.","invite_invalid":"Ссылка недействительна или отозвана.","delete":"Удалить","delete_folder_confirm":"Удалить папку со всеми словами?","invite_rights":"Какие права дать?","editor_right":"Может добавлять и менять слова","member_right":"Может только учиться","link_ready":"Ссылка готова. Её можно переслать:","revoke":"Отозвать все ссылки","revoked":"Ссылки отозваны","remove_member":"Удалить участника из папки?","create_first":"Сначала создайте первую папку.","flow_expired":"Действие устарело. Начните заново.","duplicates":"Найдены дубли: {count}.","skip":"Пропустить","add_separate":"Добавить отдельной карточкой","add_confirm":"Добавить {count} слов в папку «{name}»?","save":"Сохранить","change_folder":"Изменить папку","add_more":"Добавить ещё","card_deleted":"Карточка удалена.","edit_word":"Изменить слово","edit_translation":"Изменить перевод","new_value":"Пришлите новое значение.","delete_card":"Удалить карточку?","stale_button":"Эта кнопка устарела. Откройте обучение снова.","answered":"Ответ уже учтён.","answer_first":"Сначала ответьте на карточку.","no_errors":"Ошибок нет.","send_one":"Пришлите слово или список.","translation_prompt":"Как переводится <b>{word}</b>?","empty_translation":"Перевод не должен быть пустым.","format_help":"Используйте «слово — перевод» или пришлите одно слово."},
"en":{"joined":"You joined the folder.","invite_invalid":"This link is invalid or revoked.","delete":"Delete","delete_folder_confirm":"Delete the folder and all its words?","invite_rights":"Which permissions should be granted?","editor_right":"Can add and edit words","member_right":"Can only study","link_ready":"The link is ready to share:","revoke":"Revoke all links","revoked":"Links revoked","remove_member":"Remove this member from the folder?","create_first":"Create your first folder first.","flow_expired":"This action expired. Start again.","duplicates":"Duplicates found: {count}.","skip":"Skip","add_separate":"Add as separate cards","add_confirm":"Add {count} words to “{name}”?","save":"Save","change_folder":"Change folder","add_more":"Add more","card_deleted":"Card deleted.","edit_word":"Edit word","edit_translation":"Edit translation","new_value":"Send the new value.","delete_card":"Delete this card?","stale_button":"This button has expired. Open learning again.","answered":"The answer was already recorded.","answer_first":"Answer the card first.","no_errors":"No mistakes.","send_one":"Send a word or a list.","translation_prompt":"How do you translate <b>{word}</b>?","empty_translation":"The translation cannot be empty.","format_help":"Use “word — translation” or send one word."},
"es":{"joined":"Te has unido a la carpeta.","invite_invalid":"El enlace no es válido o fue revocado.","delete":"Eliminar","delete_folder_confirm":"¿Eliminar la carpeta y todas sus palabras?","invite_rights":"¿Qué permisos quieres dar?","editor_right":"Puede añadir y editar palabras","member_right":"Solo puede estudiar","link_ready":"El enlace está listo para compartir:","revoke":"Revocar todos los enlaces","revoked":"Enlaces revocados","remove_member":"¿Eliminar este miembro de la carpeta?","create_first":"Primero crea tu primera carpeta.","flow_expired":"Esta acción ha caducado. Empieza de nuevo.","duplicates":"Duplicados encontrados: {count}.","skip":"Omitir","add_separate":"Añadir como tarjetas separadas","add_confirm":"¿Añadir {count} palabras a «{name}»?","save":"Guardar","change_folder":"Cambiar carpeta","add_more":"Añadir más","card_deleted":"Tarjeta eliminada.","edit_word":"Editar palabra","edit_translation":"Editar traducción","new_value":"Envía el nuevo valor.","delete_card":"¿Eliminar esta tarjeta?","stale_button":"Este botón ha caducado. Abre el aprendizaje de nuevo.","answered":"La respuesta ya fue registrada.","answer_first":"Responde primero a la tarjeta.","no_errors":"No hay errores.","send_one":"Envía una palabra o una lista.","translation_prompt":"¿Cómo se traduce <b>{word}</b>?","empty_translation":"La traducción no puede estar vacía.","format_help":"Usa «palabra — traducción» o envía una palabra."},
"fr":{"joined":"Vous avez rejoint le dossier.","invite_invalid":"Ce lien est invalide ou révoqué.","delete":"Supprimer","delete_folder_confirm":"Supprimer le dossier et tous ses mots ?","invite_rights":"Quels droits accorder ?","editor_right":"Peut ajouter et modifier des mots","member_right":"Peut seulement apprendre","link_ready":"Le lien est prêt à être partagé :","revoke":"Révoquer tous les liens","revoked":"Liens révoqués","remove_member":"Retirer ce membre du dossier ?","create_first":"Créez d’abord votre premier dossier.","flow_expired":"Cette action a expiré. Recommencez.","duplicates":"Doublons trouvés : {count}.","skip":"Ignorer","add_separate":"Ajouter comme fiches séparées","add_confirm":"Ajouter {count} mots à « {name} » ?","save":"Enregistrer","change_folder":"Changer de dossier","add_more":"Ajouter encore","card_deleted":"Fiche supprimée.","edit_word":"Modifier le mot","edit_translation":"Modifier la traduction","new_value":"Envoyez la nouvelle valeur.","delete_card":"Supprimer cette fiche ?","stale_button":"Ce bouton a expiré. Relancez l’apprentissage.","answered":"La réponse a déjà été enregistrée.","answer_first":"Répondez d’abord à la fiche.","no_errors":"Aucune erreur.","send_one":"Envoyez un mot ou une liste.","translation_prompt":"Comment traduire <b>{word}</b> ?","empty_translation":"La traduction ne peut pas être vide.","format_help":"Utilisez « mot — traduction » ou envoyez un mot."},
"zh":{"joined":"你已加入文件夹。","invite_invalid":"链接无效或已撤销。","delete":"删除","delete_folder_confirm":"删除文件夹及其中所有单词？","invite_rights":"授予哪些权限？","editor_right":"可以添加和修改单词","member_right":"只能学习","link_ready":"链接已生成，可以分享：","revoke":"撤销所有链接","revoked":"链接已撤销","remove_member":"将此成员移出文件夹？","create_first":"请先创建第一个文件夹。","flow_expired":"此操作已过期，请重新开始。","duplicates":"发现 {count} 个重复项。","skip":"跳过","add_separate":"作为单独卡片添加","add_confirm":"将 {count} 个单词添加到“{name}”？","save":"保存","change_folder":"更换文件夹","add_more":"继续添加","card_deleted":"卡片已删除。","edit_word":"修改单词","edit_translation":"修改翻译","new_value":"发送新内容。","delete_card":"删除此卡片？","stale_button":"此按钮已过期，请重新进入学习。","answered":"答案已记录。","answer_first":"请先回答卡片。","no_errors":"没有错误。","send_one":"发送一个单词或列表。","translation_prompt":"<b>{word}</b> 怎么翻译？","empty_translation":"翻译不能为空。","format_help":"请使用“单词 — 翻译”格式，或发送一个单词。"},
"ar":{"joined":"انضممت إلى المجلد.","invite_invalid":"الرابط غير صالح أو تم إلغاؤه.","delete":"حذف","delete_folder_confirm":"حذف المجلد وكل كلماته؟","invite_rights":"ما الصلاحيات المطلوبة؟","editor_right":"يمكنه إضافة الكلمات وتعديلها","member_right":"يمكنه التعلم فقط","link_ready":"الرابط جاهز للمشاركة:","revoke":"إلغاء جميع الروابط","revoked":"تم إلغاء الروابط","remove_member":"إزالة هذا العضو من المجلد؟","create_first":"أنشئ مجلدك الأول أولًا.","flow_expired":"انتهت صلاحية هذا الإجراء. ابدأ من جديد.","duplicates":"تم العثور على {count} تكرار.","skip":"تخطي","add_separate":"إضافة كبطاقات منفصلة","add_confirm":"إضافة {count} كلمة إلى «{name}»؟","save":"حفظ","change_folder":"تغيير المجلد","add_more":"إضافة المزيد","card_deleted":"تم حذف البطاقة.","edit_word":"تعديل الكلمة","edit_translation":"تعديل الترجمة","new_value":"أرسل القيمة الجديدة.","delete_card":"حذف هذه البطاقة؟","stale_button":"انتهت صلاحية هذا الزر. افتح التعلم مجددًا.","answered":"تم تسجيل الإجابة بالفعل.","answer_first":"أجب عن البطاقة أولًا.","no_errors":"لا توجد أخطاء.","send_one":"أرسل كلمة أو قائمة.","translation_prompt":"ما ترجمة <b>{word}</b>؟","empty_translation":"لا يمكن أن تكون الترجمة فارغة.","format_help":"استخدم «كلمة — ترجمة» أو أرسل كلمة واحدة."}}
for _locale, _values in _MORE.items():
    TEXT[_locale].update(_values)

_SHELL = {
    "ru": {
        "shell_welcome": "<b>Slovo</b> — учить слова стало проще ✨\n\nСоздавайте папки, добавляйте слова вместе с преподавателем и повторяйте их в удобном темпе. Всё обучение теперь находится в приложении — нажмите кнопку ниже.",
        "open_app": "🚀 Открыть Slovo",
        "profile_hint": "Вся работа со словами и настройками доступна в приложении.",
    },
    "en": {
        "shell_welcome": "<b>Slovo</b> makes learning words simple ✨\n\nCreate folders, add words together with your teacher and review them at your own pace. Everything is now inside the app — tap the button below.",
        "open_app": "🚀 Open Slovo",
        "profile_hint": "Words, learning and settings are available in the app.",
    },
    "es": {
        "shell_welcome": "<b>Slovo</b> hace que aprender palabras sea más fácil ✨\n\nCrea carpetas, añade palabras con tu profesor y repásalas a tu ritmo. Todo está ahora dentro de la aplicación — pulsa el botón de abajo.",
        "open_app": "🚀 Abrir Slovo",
        "profile_hint": "Las palabras, el aprendizaje y los ajustes están disponibles en la aplicación.",
    },
    "fr": {
        "shell_welcome": "<b>Slovo</b> simplifie l’apprentissage des mots ✨\n\nCréez des dossiers, ajoutez des mots avec votre professeur et révisez à votre rythme. Tout se trouve maintenant dans l’application — appuyez ci-dessous.",
        "open_app": "🚀 Ouvrir Slovo",
        "profile_hint": "Les mots, l’apprentissage et les réglages sont disponibles dans l’application.",
    },
    "zh": {
        "shell_welcome": "<b>Slovo</b> 让单词学习更简单 ✨\n\n创建文件夹，与老师一起添加单词，并按自己的节奏复习。所有功能现在都在应用中——点击下方按钮。",
        "open_app": "🚀 打开 Slovo",
        "profile_hint": "单词、学习和设置均可在应用中使用。",
    },
    "ar": {
        "shell_welcome": "<b>Slovo</b> يجعل تعلّم الكلمات أسهل ✨\n\nأنشئ المجلدات وأضف الكلمات مع معلمك وراجعها بالوتيرة المناسبة لك. كل شيء متاح الآن داخل التطبيق — اضغط الزر أدناه.",
        "open_app": "🚀 فتح Slovo",
        "profile_hint": "الكلمات والتعلّم والإعدادات متاحة داخل التطبيق.",
    },
}
for _locale, _values in _SHELL.items():
    TEXT[_locale].update(_values)
for _locale, _message in {
    "ru":"В одной папке может быть не больше 50 слов.",
    "en":"A folder can contain no more than 50 words.",
    "es":"Una carpeta puede contener como máximo 50 palabras.",
    "fr":"Un dossier peut contenir au maximum 50 mots.",
    "zh":"每个文件夹最多可包含 50 个单词。",
    "ar":"يمكن أن يحتوي المجلد على 50 كلمة كحد أقصى.",
}.items():
    TEXT[_locale]["folder_word_limit"]=_message


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.init()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c; c.commit()
        except Exception:
            c.rollback(); raise
        finally: c.close()

    def init(self):
        with self.conn() as c:
            c.executescript('''
CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, name TEXT, last_folder_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_id INTEGER NOT NULL REFERENCES users(telegram_id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS memberships (folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(telegram_id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('owner','editor','member')), PRIMARY KEY(folder_id,user_id));
CREATE TABLE IF NOT EXISTS invitations (token TEXT PRIMARY KEY, folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE, role TEXT NOT NULL, created_by INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE, term TEXT NOT NULL, translation TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS progress (user_id INTEGER, card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE, level INTEGER NOT NULL DEFAULT 0, due_date TEXT, last_success TEXT, PRIMARY KEY(user_id, card_id));
CREATE TABLE IF NOT EXISTS drafts (user_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, folder_id INTEGER NOT NULL, mode TEXT NOT NULL, queue TEXT NOT NULL, pos INTEGER DEFAULT 0, current_card INTEGER, phase TEXT NOT NULL DEFAULT 'ask', answered INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]', extra_counts TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS study_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL,
 card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
 attempt_no INTEGER NOT NULL DEFAULT 1,
 correct INTEGER NOT NULL CHECK(correct IN (0,1)),
 first_attempt INTEGER NOT NULL CHECK(first_attempt IN (0,1)),
 answer_text TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(session_id, card_id, attempt_no)
);
CREATE TABLE IF NOT EXISTS pronunciation_cache (
 cache_key TEXT PRIMARY KEY,
 transcription TEXT,
 audio_url TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS catalog_categories (
 slug TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 icon TEXT NOT NULL,
 position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_sets (
 slug TEXT PRIMARY KEY,
 category_slug TEXT NOT NULL REFERENCES catalog_categories(slug),
 folder_id INTEGER NOT NULL UNIQUE REFERENCES folders(id) ON DELETE CASCADE,
 title TEXT NOT NULL,
 description TEXT NOT NULL,
 icon TEXT NOT NULL,
 position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_cards (
 card_key TEXT PRIMARY KEY,
 set_slug TEXT NOT NULL REFERENCES catalog_sets(slug) ON DELETE CASCADE,
 card_id INTEGER NOT NULL UNIQUE REFERENCES cards(id) ON DELETE CASCADE,
 position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_set_subscriptions (
 user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 set_slug TEXT NOT NULL REFERENCES catalog_sets(slug) ON DELETE CASCADE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 attached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(user_id,set_slug)
);
CREATE TABLE IF NOT EXISTS game_rounds (
 id TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
 game_type TEXT NOT NULL CHECK(game_type IN ('match','listen','build')),
 snapshot TEXT NOT NULL,
 state TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','paused','completed','abandoned')),
 active_seconds INTEGER NOT NULL DEFAULT 0,
 started_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE TABLE IF NOT EXISTS game_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 round_id TEXT NOT NULL REFERENCES game_rounds(id) ON DELETE CASCADE,
 event_id TEXT NOT NULL,
 action TEXT NOT NULL,
 card_id INTEGER,
 payload TEXT NOT NULL DEFAULT '{}',
 response TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(round_id,event_id)
);
CREATE TABLE IF NOT EXISTS topics (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 is_system INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
 position INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(folder_id,name)
);
CREATE TABLE IF NOT EXISTS invite_claims (
 token TEXT NOT NULL REFERENCES invitations(token) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('accepted','declined')),
 responded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(token,user_id)
);
CREATE TABLE IF NOT EXISTS classes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 teacher_user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 language TEXT NOT NULL DEFAULT 'en',
 level TEXT,
 description TEXT,
 invite_code TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS class_members (
 class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','left','removed')),
 joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(class_id,user_id)
);
CREATE TABLE IF NOT EXISTS assignments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
 created_by INTEGER NOT NULL REFERENCES users(telegram_id),
 title TEXT NOT NULL,
 folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
 topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
 scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('all','selected')),
 deadline TEXT,
 description TEXT,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS assignment_recipients (
 assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','started','completed')),
 started_at TEXT,
 completed_at TEXT,
 PRIMARY KEY(assignment_id,user_id)
);
''')
            user_cols={r[1] for r in c.execute("PRAGMA table_info(users)")}
            folder_cols={r[1] for r in c.execute("PRAGMA table_info(folders)")}
            card_cols={r[1] for r in c.execute("PRAGMA table_info(cards)")}
            progress_cols={r[1] for r in c.execute("PRAGMA table_info(progress)")}
            session_cols={r[1] for r in c.execute("PRAGMA table_info(sessions)")}
            game_cols={r[1] for r in c.execute("PRAGMA table_info(game_rounds)")}
            if 'locale' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT 'ru'")
            if 'timezone_offset' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN timezone_offset INTEGER NOT NULL DEFAULT 0")
            if 'telegram_avatar_url' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN telegram_avatar_url TEXT")
            if 'custom_avatar_key' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN custom_avatar_key TEXT")
            if 'usage_role' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN usage_role TEXT NOT NULL DEFAULT 'unknown'")
            if 'acquisition_source' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN acquisition_source TEXT")
            if 'campaign' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN campaign TEXT")
            if 'ref_code' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN ref_code TEXT")
            if 'acquired_at' not in user_cols:c.execute("ALTER TABLE users ADD COLUMN acquired_at TEXT")
            for column,definition in {
                'personal_ref_code':'TEXT','referrer_user_id':'INTEGER','declared_source':'TEXT',
                'onboarding_started_at':'TEXT','onboarding_completed_at':'TEXT',
                'onboarding_step':'INTEGER NOT NULL DEFAULT 0','purposes':"TEXT NOT NULL DEFAULT '[]'",
                'learning_languages':"TEXT NOT NULL DEFAULT '[]'",'levels':"TEXT NOT NULL DEFAULT '[]'",
                'channel_subscribed':'INTEGER NOT NULL DEFAULT 0','channel_checked_at':'TEXT'
            }.items():
                if column not in user_cols:c.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
            if 'source_lang' not in folder_cols:c.execute("ALTER TABLE folders ADD COLUMN source_lang TEXT NOT NULL DEFAULT 'en'")
            if 'target_lang' not in folder_cols:c.execute("ALTER TABLE folders ADD COLUMN target_lang TEXT NOT NULL DEFAULT 'ru'")
            if 'is_official' not in folder_cols:c.execute("ALTER TABLE folders ADD COLUMN is_official INTEGER NOT NULL DEFAULT 0")
            for column in ('transcription','example','example_translation','audio_url'):
                if column not in card_cols:c.execute(f"ALTER TABLE cards ADD COLUMN {column} TEXT")
            if 'synonyms' not in card_cols:c.execute("ALTER TABLE cards ADD COLUMN synonyms TEXT NOT NULL DEFAULT '[]'")
            if 'topic_id' not in card_cols:c.execute("ALTER TABLE cards ADD COLUMN topic_id INTEGER")
            if 'first_learned_at' not in progress_cols:c.execute("ALTER TABLE progress ADD COLUMN first_learned_at TEXT")
            for column,definition in {'success_count':'INTEGER NOT NULL DEFAULT 0','error_count':'INTEGER NOT NULL DEFAULT 0','distinct_days':'INTEGER NOT NULL DEFAULT 0','mastery_score':'INTEGER NOT NULL DEFAULT 0','mastery_status':"TEXT NOT NULL DEFAULT 'new'"}.items():
                if column not in progress_cols:c.execute(f"ALTER TABLE progress ADD COLUMN {column} {definition}")
            additions={
                'study_format': "TEXT NOT NULL DEFAULT 'cards'",
                'started_at': 'TEXT', 'completed_at': 'TEXT',
                'timezone_offset': 'INTEGER NOT NULL DEFAULT 0',
                'active_seconds': 'INTEGER NOT NULL DEFAULT 0',
                'last_activity_at': 'TEXT',
            }
            for column,definition in additions.items():
                if column not in session_cols:c.execute(f"ALTER TABLE sessions ADD COLUMN {column} {definition}")
            for column in ('topic_id','assignment_id'):
                if column not in session_cols:c.execute(f"ALTER TABLE sessions ADD COLUMN {column} INTEGER")
                if column not in game_cols:c.execute(f"ALTER TABLE game_rounds ADD COLUMN {column} INTEGER")
            c.execute("UPDATE sessions SET errors='[]' WHERE errors IS NULL OR errors='' ")
            c.execute("UPDATE sessions SET extra_counts='{}' WHERE extra_counts IS NULL OR extra_counts='' ")
            c.execute("UPDATE sessions SET started_at=COALESCE(started_at,created_at)")
            c.executescript('''
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id,folder_id);
CREATE INDEX IF NOT EXISTS idx_cards_folder ON cards(folder_id,id);
CREATE INDEX IF NOT EXISTS idx_progress_due ON progress(user_id,due_date,card_id);
CREATE INDEX IF NOT EXISTS idx_events_user_date ON study_events(user_id,created_at);
CREATE INDEX IF NOT EXISTS idx_events_session_card ON study_events(session_id,card_id);
CREATE INDEX IF NOT EXISTS idx_catalog_sets_category ON catalog_sets(category_slug,position);
CREATE INDEX IF NOT EXISTS idx_catalog_cards_set ON catalog_cards(set_slug,position);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON user_set_subscriptions(user_id,active,set_slug);
CREATE INDEX IF NOT EXISTS idx_game_rounds_user ON game_rounds(user_id,folder_id,status,updated_at);
CREATE INDEX IF NOT EXISTS idx_game_events_round ON game_events(round_id,id);
DROP TRIGGER IF EXISTS limit_cards_per_folder;
CREATE TRIGGER IF NOT EXISTS limit_cards_per_topic
BEFORE INSERT ON cards
WHEN NEW.topic_id IS NOT NULL AND (SELECT COUNT(*) FROM cards WHERE topic_id=NEW.topic_id) >= 30
BEGIN
 SELECT RAISE(ABORT,'topic_word_limit');
END;
CREATE INDEX IF NOT EXISTS idx_topics_folder ON topics(folder_id,position,id);
CREATE INDEX IF NOT EXISTS idx_cards_topic ON cards(topic_id,id);
CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_user_id,active,id);
CREATE INDEX IF NOT EXISTS idx_class_members_user ON class_members(user_id,status,class_id);
CREATE INDEX IF NOT EXISTS idx_assignments_class ON assignments(class_id,active,id);
CREATE INDEX IF NOT EXISTS idx_assignment_recipients_user ON assignment_recipients(user_id,status,assignment_id);
CREATE TABLE IF NOT EXISTS catalog_card_translations (
 card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
 language TEXT NOT NULL,
 term TEXT NOT NULL,
 synonyms TEXT NOT NULL DEFAULT '[]',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(card_id,language)
);
CREATE INDEX IF NOT EXISTS idx_catalog_card_translations_language ON catalog_card_translations(language,card_id);
''')
            migration_path=Path(__file__).resolve().parent/'migrations'/'001_product_analytics.sql'
            if migration_path.exists():
                c.executescript(migration_path.read_text(encoding='utf-8'))
                c.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES('001_product_analytics')")
            expansion_path=Path(__file__).resolve().parent/'migrations'/'002_product_expansion.sql'
            if expansion_path.exists():c.executescript(expansion_path.read_text(encoding='utf-8'))
            learning_profile_path=Path(__file__).resolve().parent/'migrations'/'003_learning_profile.sql'
            learning_profile_pending=not c.execute("SELECT 1 FROM schema_migrations WHERE version='003_learning_profile'").fetchone()
            if learning_profile_pending:
                # Every user present at rollout sees the questionnaire once. The
                # migration marker prevents later restarts from asking again.
                c.execute("UPDATE users SET onboarding_completed_at=NULL,onboarding_step=0")
                if learning_profile_path.exists():c.executescript(learning_profile_path.read_text(encoding='utf-8'))
                else:c.execute("INSERT INTO schema_migrations(version) VALUES('003_learning_profile')")
            enrichment_path=Path(__file__).resolve().parent/'migrations'/'004_language_enrichment.sql'
            if enrichment_path.exists():c.executescript(enrichment_path.read_text(encoding='utf-8'))
            from catalog_data import seed_catalog
            seed_catalog(c)
            # Users that existed before attribution was introduced are organic.
            c.execute("""UPDATE users SET acquisition_source='organic',campaign='organic',ref_code='organic',
acquired_at=COALESCE(created_at,CURRENT_TIMESTAMP) WHERE acquired_at IS NULL""")
            for row in c.execute("SELECT telegram_id FROM users WHERE personal_ref_code IS NULL OR personal_ref_code='' ").fetchall():
                c.execute("UPDATE users SET personal_ref_code=? WHERE telegram_id=?",(f"u{row['telegram_id']:x}",row['telegram_id']))
            # Backfill old cards without losing data: system topics are chunked at 30 words.
            for folder in c.execute("SELECT id,owner_id FROM folders ORDER BY id").fetchall():
                orphan_ids=[r[0] for r in c.execute("SELECT id FROM cards WHERE folder_id=? AND topic_id IS NULL ORDER BY id",(folder['id'],)).fetchall()]
                for index in range(0,len(orphan_ids),MAX_CARDS_PER_TOPIC):
                    name='Без темы' if index==0 else f"Без темы {index//MAX_CARDS_PER_TOPIC+1}"
                    c.execute("INSERT OR IGNORE INTO topics(folder_id,name,is_system,position,created_by) VALUES(?,?,1,?,?)",(folder['id'],name,index//MAX_CARDS_PER_TOPIC,folder['owner_id']))
                    topic_id=c.execute("SELECT id FROM topics WHERE folder_id=? AND name=?",(folder['id'],name)).fetchone()[0]
                    marks=','.join('?' for _ in orphan_ids[index:index+MAX_CARDS_PER_TOPIC])
                    if marks:c.execute(f"UPDATE cards SET topic_id=? WHERE id IN ({marks})",[topic_id]+orphan_ids[index:index+MAX_CARDS_PER_TOPIC])
            c.execute("UPDATE folders SET is_official=1 WHERE id IN (SELECT folder_id FROM catalog_sets)")

    def user(self, tg_id, name="", telegram_avatar_url=None, ref_code='organic', acquisition_source='organic'):
        with self.conn() as c:
            existing=c.execute("SELECT 1 FROM users WHERE telegram_id=?",(tg_id,)).fetchone()
            c.execute('''INSERT INTO users(telegram_id,name,telegram_avatar_url,acquisition_source,campaign,ref_code,acquired_at) VALUES(?,?,?,?,?,?,?)
ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name,
telegram_avatar_url=COALESCE(excluded.telegram_avatar_url,users.telegram_avatar_url)''',
                      (tg_id,name,telegram_avatar_url,acquisition_source,ref_code,ref_code,datetime.now(timezone.utc).isoformat()))
            c.execute("UPDATE users SET personal_ref_code=COALESCE(NULLIF(personal_ref_code,''),?) WHERE telegram_id=?",(f"u{tg_id:x}",tg_id))
            return existing is None
    def avatar(self,u):
        with self.conn() as c:return c.execute("SELECT custom_avatar_key,telegram_avatar_url FROM users WHERE telegram_id=?",(u,)).fetchone()
    def set_custom_avatar(self,u,key):
        with self.conn() as c:c.execute("UPDATE users SET custom_avatar_key=? WHERE telegram_id=?",(key,u))
    def set_draft(self,u,k,p):
        with self.conn() as c: c.execute("INSERT INTO drafts(user_id,kind,payload) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET kind=excluded.kind,payload=excluded.payload,updated_at=CURRENT_TIMESTAMP",(u,k,p))
    def draft(self,u):
        with self.conn() as c: return c.execute("SELECT * FROM drafts WHERE user_id=?",(u,)).fetchone()
    def clear_draft(self,u):
        with self.conn() as c:c.execute("DELETE FROM drafts WHERE user_id=?",(u,))
    def role(self,u,f):
        with self.conn() as c:
            r=c.execute("SELECT role FROM memberships WHERE user_id=? AND folder_id=?",(u,f)).fetchone(); return r[0] if r else None
    def folders(self,u):
        with self.conn() as c:return c.execute('''SELECT f.*,m.role,cs.slug set_slug,cs.category_slug,cs.description,cs.icon set_icon
FROM folders f JOIN memberships m ON m.folder_id=f.id LEFT JOIN catalog_sets cs ON cs.folder_id=f.id
WHERE m.user_id=? ORDER BY (f.id=(SELECT last_folder_id FROM users WHERE telegram_id=?)) DESC,cs.slug IS NOT NULL,f.name''',(u,u)).fetchall()
    def folder_summaries(self,u):
        """Return folder cards with counts in one query (the old bootstrap did 2N+ queries)."""
        today=date.today().isoformat()
        with self.conn() as c:
            return c.execute('''
SELECT f.*,m.role,cs.slug set_slug,cs.category_slug,cs.description,cs.icon set_icon,COUNT(c.id) word_count,
 SUM(CASE WHEN c.id IS NOT NULL AND (p.card_id IS NULL OR p.due_date IS NULL OR p.due_date<=?) THEN 1 ELSE 0 END) due_count,
 SUM(CASE WHEN p.last_success IS NOT NULL THEN 1 ELSE 0 END) learned_count
FROM memberships m JOIN folders f ON f.id=m.folder_id LEFT JOIN catalog_sets cs ON cs.folder_id=f.id
LEFT JOIN cards c ON c.folder_id=f.id
LEFT JOIN progress p ON p.card_id=c.id AND p.user_id=?
WHERE m.user_id=? GROUP BY f.id,m.role
ORDER BY (f.id=(SELECT last_folder_id FROM users WHERE telegram_id=?)) DESC,cs.slug IS NOT NULL,f.name
''',(today,u,u,u)).fetchall()
    def folder(self,u,f):
        with self.conn() as c:return c.execute('''SELECT f.*,m.role,cs.slug set_slug,cs.category_slug,cs.description,cs.icon set_icon
FROM folders f JOIN memberships m ON m.folder_id=f.id LEFT JOIN catalog_sets cs ON cs.folder_id=f.id
WHERE f.id=? AND m.user_id=?''',(f,u)).fetchone()
    def locale(self,u):
        with self.conn() as c:
            r=c.execute("SELECT locale FROM users WHERE telegram_id=?",(u,)).fetchone();return r[0] if r else 'ru'
    def set_locale(self,u,locale):
        with self.conn() as c:c.execute("UPDATE users SET locale=? WHERE telegram_id=?",(locale,u))
    def set_timezone(self,u,offset):
        with self.conn() as c:c.execute("UPDATE users SET timezone_offset=? WHERE telegram_id=?",(max(-840,min(840,int(offset))),u))
    def user_profile(self,u):
        with self.conn() as c:return c.execute("SELECT * FROM users WHERE telegram_id=?",(u,)).fetchone()
    def save_onboarding(self,u,step,usage_role=None,purposes=None,languages=None,levels=None,declared_source=None,complete=False):
        import json
        with self.conn() as c:
            c.execute('''UPDATE users SET onboarding_started_at=COALESCE(onboarding_started_at,CURRENT_TIMESTAMP),onboarding_step=?,
usage_role=COALESCE(?,usage_role),purposes=COALESCE(?,purposes),learning_languages=COALESCE(?,learning_languages),
levels=COALESCE(?,levels),declared_source=COALESCE(?,declared_source),onboarding_completed_at=CASE WHEN ? THEN COALESCE(onboarding_completed_at,CURRENT_TIMESTAMP) ELSE onboarding_completed_at END
WHERE telegram_id=?''',(step,usage_role,json.dumps(purposes,ensure_ascii=False) if purposes is not None else None,json.dumps(languages,ensure_ascii=False) if languages is not None else None,json.dumps(levels,ensure_ascii=False) if levels is not None else None,declared_source,int(complete),u))
    def referral_count(self,u):
        with self.conn() as c:return c.execute("SELECT COUNT(*) FROM users WHERE referrer_user_id=?",(u,)).fetchone()[0]
    def apply_referral(self,u,code):
        if not code:return False
        with self.conn() as c:
            owner=c.execute("SELECT telegram_id FROM users WHERE personal_ref_code=?",(code,)).fetchone()
            if not owner or owner[0]==u:return False
            cur=c.execute("UPDATE users SET referrer_user_id=?,acquisition_source='referral',campaign=?,ref_code=? WHERE telegram_id=? AND referrer_user_id IS NULL",(owner[0],f"ref_{code}",f"ref_{code}",u));return bool(cur.rowcount)
    def set_channel_state(self,u,subscribed):
        with self.conn() as c:
            c.execute("UPDATE users SET channel_subscribed=?,channel_checked_at=CURRENT_TIMESTAMP WHERE telegram_id=?",(int(bool(subscribed)),u))
            if not subscribed:
                c.execute("UPDATE user_set_subscriptions SET active=0,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",(u,))
                c.execute("DELETE FROM memberships WHERE user_id=? AND role='member' AND folder_id IN (SELECT folder_id FROM catalog_sets)",(u,))
    def request_result(self,u,operation,request_id):
        if not request_id:return None
        with self.conn() as c:
            row=c.execute("SELECT response FROM request_deduplication WHERE user_id=? AND operation=? AND request_id=?",(u,operation,request_id)).fetchone()
            return row[0] if row else None
    def save_request_result(self,u,operation,request_id,response):
        if not request_id:return
        with self.conn() as c:c.execute("INSERT OR IGNORE INTO request_deduplication(user_id,operation,request_id,response) VALUES(?,?,?,?)",(u,operation,request_id,response))
    def create_folder(self,u,name,source_lang='en',target_lang='ru'):
        with self.conn() as c:
            cur=c.execute("INSERT INTO folders(name,owner_id,source_lang,target_lang) VALUES(?,?,?,?)",(name,u,source_lang,target_lang)); f=cur.lastrowid;c.execute("INSERT INTO memberships VALUES(?,?,?)",(f,u,"owner"));c.execute("INSERT INTO topics(folder_id,name,is_system,position,created_by) VALUES(?, 'Без темы',1,0,?)",(f,u));return f
    def topics(self,u,f):
        with self.conn() as c:return c.execute('''SELECT t.*,COUNT(cd.id) word_count,SUM(CASE WHEN p.last_success IS NOT NULL THEN 1 ELSE 0 END) learned_count
FROM topics t JOIN memberships m ON m.folder_id=t.folder_id AND m.user_id=? LEFT JOIN cards cd ON cd.topic_id=t.id
LEFT JOIN progress p ON p.card_id=cd.id AND p.user_id=? WHERE t.folder_id=? GROUP BY t.id ORDER BY t.position,t.id''',(u,u,f)).fetchall()
    def topic(self,u,topic_id):
        with self.conn() as c:return c.execute("SELECT t.*,m.role FROM topics t JOIN memberships m ON m.folder_id=t.folder_id WHERE t.id=? AND m.user_id=?",(topic_id,u)).fetchone()
    def create_topic(self,u,f,name):
        with self.conn() as c:
            position=c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM topics WHERE folder_id=?",(f,)).fetchone()[0]
            cur=c.execute("INSERT INTO topics(folder_id,name,position,created_by) VALUES(?,?,?,?)",(f,name,position,u));return cur.lastrowid
    def rename_topic(self,topic_id,name):
        with self.conn() as c:c.execute("UPDATE topics SET name=? WHERE id=?",(name,topic_id))
    def move_card(self,card_id,topic_id):
        with self.conn() as c:
            topic=c.execute("SELECT folder_id,(SELECT COUNT(*) FROM cards WHERE topic_id=?) n FROM topics WHERE id=?",(topic_id,topic_id)).fetchone()
            card=c.execute("SELECT folder_id FROM cards WHERE id=?",(card_id,)).fetchone()
            if not topic or not card or topic['folder_id']!=card['folder_id']:raise ValueError('invalid_topic')
            if topic['n']>=MAX_CARDS_PER_TOPIC:raise ValueError('topic_word_limit')
            c.execute("UPDATE cards SET topic_id=? WHERE id=?",(topic_id,card_id))
    def delete_topic(self,topic_id,target_topic_id=None):
        with self.conn() as c:
            row=c.execute("SELECT folder_id,is_system FROM topics WHERE id=?",(topic_id,)).fetchone()
            if not row or row['is_system']:raise ValueError('system_topic')
            count=c.execute("SELECT COUNT(*) FROM cards WHERE topic_id=?",(topic_id,)).fetchone()[0]
            if count:
                if not target_topic_id:raise ValueError('topic_not_empty')
                target=c.execute("SELECT folder_id,(SELECT COUNT(*) FROM cards WHERE topic_id=?) n FROM topics WHERE id=?",(target_topic_id,target_topic_id)).fetchone()
                if not target or target['folder_id']!=row['folder_id']:raise ValueError('invalid_topic')
                if target['n']+count>MAX_CARDS_PER_TOPIC:raise ValueError('topic_word_limit')
                c.execute("UPDATE cards SET topic_id=? WHERE topic_id=?",(target_topic_id,topic_id))
            c.execute("DELETE FROM topics WHERE id=?",(topic_id,))
    def catalog_categories(self):
        with self.conn() as c:return c.execute("SELECT * FROM catalog_categories ORDER BY position,slug").fetchall()
    def catalog_sets(self,u,category='',query=''):
        params=[u,u]; where=[]
        if category:
            where.append("cs.category_slug=?");params.append(category)
        if query:
            pattern=f"%{query}%";where.append('''(lower(cs.title) LIKE lower(?) OR lower(cs.description) LIKE lower(?) OR EXISTS (
SELECT 1 FROM catalog_cards cc JOIN cards sc ON sc.id=cc.card_id
WHERE cc.set_slug=cs.slug AND (lower(sc.term) LIKE lower(?) OR lower(sc.translation) LIKE lower(?))))''');params.extend([pattern]*4)
        condition=(" WHERE "+" AND ".join(where)) if where else ""
        with self.conn() as c:return c.execute(f'''SELECT cs.*,ccat.title category_title,ccat.icon category_icon,
25 word_count,COALESCE(sub.active,0) added,
SUM(CASE WHEN p.last_success IS NOT NULL THEN 1 ELSE 0 END) learned_count,
SUM(CASE WHEN p.card_id IS NULL OR p.due_date IS NULL OR p.due_date<=date('now') THEN 1 ELSE 0 END) due_count
FROM catalog_sets cs JOIN catalog_categories ccat ON ccat.slug=cs.category_slug
JOIN catalog_cards cc ON cc.set_slug=cs.slug
LEFT JOIN progress p ON p.card_id=cc.card_id AND p.user_id=?
LEFT JOIN user_set_subscriptions sub ON sub.set_slug=cs.slug AND sub.user_id=?
{condition} GROUP BY cs.slug ORDER BY ccat.position,cs.position''',params).fetchall()
    def catalog_set(self,u,slug):
        rows=self.catalog_sets(u)
        return next((row for row in rows if row['slug']==slug),None)
    def catalog_cards(self,slug):
        with self.conn() as c:return c.execute('''SELECT c.* FROM catalog_cards cc JOIN cards c ON c.id=cc.card_id
WHERE cc.set_slug=? ORDER BY cc.position''',(slug,)).fetchall()
    def catalog_translation(self,card_id,language):
        with self.conn() as c:return c.execute("SELECT * FROM catalog_card_translations WHERE card_id=? AND language=?",(card_id,language)).fetchone()
    def save_catalog_translation(self,card_id,language,term,synonyms=None):
        with self.conn() as c:c.execute('''INSERT INTO catalog_card_translations(card_id,language,term,synonyms) VALUES(?,?,?,?)
ON CONFLICT(card_id,language) DO UPDATE SET term=excluded.term,synonyms=excluded.synonyms,updated_at=CURRENT_TIMESTAMP''',(card_id,language,term,json.dumps(synonyms or [],ensure_ascii=False)))
    def subscribe_set(self,u,slug):
        with self.conn() as c:
            row=c.execute("SELECT folder_id FROM catalog_sets WHERE slug=?",(slug,)).fetchone()
            if not row:return None
            c.execute("INSERT INTO memberships(folder_id,user_id,role) VALUES(?,?,?) ON CONFLICT(folder_id,user_id) DO UPDATE SET role='member'",(row[0],u,'member'))
            c.execute('''INSERT INTO user_set_subscriptions(user_id,set_slug,active) VALUES(?,?,1)
ON CONFLICT(user_id,set_slug) DO UPDATE SET active=1,updated_at=CURRENT_TIMESTAMP''',(u,slug))
            return row[0]
    def unsubscribe_set(self,u,slug):
        with self.conn() as c:
            row=c.execute("SELECT folder_id FROM catalog_sets WHERE slug=?",(slug,)).fetchone()
            if not row:return False
            c.execute("UPDATE user_set_subscriptions SET active=0,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND set_slug=?",(u,slug))
            c.execute("DELETE FROM memberships WHERE folder_id=? AND user_id=? AND role='member'",(row[0],u))
            return True
    def copy_catalog_set(self,u,slug):
        with self.conn() as c:
            source=c.execute("SELECT folder_id,title FROM catalog_sets WHERE slug=?",(slug,)).fetchone()
            if not source:return None
            folder_id=c.execute("INSERT INTO folders(name,owner_id,source_lang,target_lang) VALUES(?,?,?,?)",(f"{source['title']} — копия",u,'en','ru')).lastrowid
            c.execute("INSERT INTO memberships(folder_id,user_id,role) VALUES(?,?,?)",(folder_id,u,'owner'))
            topic_id=c.execute("INSERT INTO topics(folder_id,name,is_system,position,created_by) VALUES(?,'Без темы',1,0,?)",(folder_id,u)).lastrowid
            c.execute('''INSERT INTO cards(folder_id,topic_id,term,translation,transcription,example,example_translation,audio_url,synonyms,created_by)
SELECT ?,?,c.term,c.translation,c.transcription,c.example,c.example_translation,c.audio_url,c.synonyms,?
FROM catalog_cards cc JOIN cards c ON c.id=cc.card_id WHERE cc.set_slug=? ORDER BY cc.position''',(folder_id,topic_id,u,slug))
            return folder_id
    def localize_catalog_copy(self,folder_id,slug,language):
        if language=='en':return
        with self.conn() as c:
            source=c.execute("SELECT target_lang FROM folders WHERE id=?",(folder_id,)).fetchone()
            if not source:return
            c.execute("UPDATE folders SET source_lang=? WHERE id=?",(language,folder_id))
            copied=c.execute("SELECT id FROM cards WHERE folder_id=? ORDER BY id",(folder_id,)).fetchall()
            originals=c.execute('''SELECT tr.term,tr.synonyms FROM catalog_cards cc
LEFT JOIN catalog_card_translations tr ON tr.card_id=cc.card_id AND tr.language=?
WHERE cc.set_slug=? ORDER BY cc.position''',(language,slug)).fetchall()
            for card,row in zip(copied,originals):
                if row['term'] is not None:
                    c.execute("UPDATE cards SET term=?,synonyms=? WHERE id=?",(row['term'],row['synonyms'],card['id']))
    def set_folder_languages(self,f,source,target):
        with self.conn() as c:c.execute("UPDATE folders SET source_lang=?,target_lang=? WHERE id=?",(source,target,f))
    def profile_stats(self,u):
        with self.conn() as c:
            folders=c.execute("SELECT count(*) FROM memberships WHERE user_id=?",(u,)).fetchone()[0]
            words=c.execute("SELECT count(*) FROM cards c JOIN memberships m ON m.folder_id=c.folder_id WHERE m.user_id=?",(u,)).fetchone()[0]
            learned=c.execute("SELECT count(*) FROM progress p JOIN cards c ON c.id=p.card_id JOIN memberships m ON m.folder_id=c.folder_id WHERE p.user_id=? AND m.user_id=? AND p.last_success IS NOT NULL",(u,u)).fetchone()[0]
            # A training is a session in which at least one answer was recorded.
            sessions=c.execute("SELECT count(DISTINCT session_id) FROM study_events WHERE user_id=?",(u,)).fetchone()[0]
            game_rounds=c.execute("SELECT count(*) FROM game_rounds WHERE user_id=? AND status='completed'",(u,)).fetchone()[0]
            return {"folders":folders,"words":words,"learned":learned,"sessions":sessions,"game_rounds":game_rounds}

    def create_game_round(self,round_id,u,f,game_type,snapshot,state,topic_id=None,assignment_id=None):
        import json
        now=datetime.now(timezone.utc).isoformat()
        with self.conn() as c:
            c.execute("INSERT INTO game_rounds(id,user_id,folder_id,game_type,snapshot,state,started_at,updated_at,topic_id,assignment_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (round_id,u,f,game_type,json.dumps(snapshot,ensure_ascii=False),json.dumps(state,ensure_ascii=False),now,now,topic_id,assignment_id))

    def game_round(self,u,round_id):
        with self.conn() as c:return c.execute("SELECT * FROM game_rounds WHERE id=? AND user_id=?",(round_id,u)).fetchone()

    def unfinished_game(self,u,f,game_type=None,topic_id=None):
        query="SELECT * FROM game_rounds WHERE user_id=? AND folder_id=? AND status IN ('in_progress','paused')"
        params=[u,f]
        if game_type:
            query+=" AND game_type=?";params.append(game_type)
        if topic_id is not None:
            query+=" AND topic_id=?";params.append(topic_id)
        query+=" ORDER BY updated_at DESC LIMIT 1"
        with self.conn() as c:return c.execute(query,params).fetchone()
    def weekly_stats(self,u,timezone_offset=0):
        offset=max(-840,min(840,int(timezone_offset)))
        now=datetime.now(timezone.utc)-timedelta(minutes=offset)
        first_day=(now.date()-timedelta(days=6)).isoformat()
        modifier=f"{(-offset):+d} minutes"
        with self.conn() as c:
            rows=c.execute('''
SELECT date(e.created_at, ?) day,COUNT(DISTINCT e.card_id) unique_words,
 SUM(CASE WHEN e.first_attempt=1 AND e.correct=1 THEN 1 ELSE 0 END) first_correct
FROM study_events e WHERE e.user_id=? AND date(e.created_at, ?)>=?
GROUP BY date(e.created_at, ?) ORDER BY day
''',(modifier,u,modifier,first_day,modifier)).fetchall()
            trainings=c.execute('''SELECT COUNT(DISTINCT session_id) FROM study_events
WHERE user_id=? AND date(created_at, ?)>=?''',(u,modifier,first_day)).fetchone()[0]
            learned=c.execute('''SELECT COUNT(*) FROM progress WHERE user_id=? AND first_learned_at IS NOT NULL
AND date(first_learned_at, ?)>=?''',(u,modifier,first_day)).fetchone()[0]
            duration_rows=c.execute('''SELECT s.id,s.active_seconds,COUNT(e.id) answers
FROM sessions s JOIN study_events e ON e.session_id=s.id
WHERE s.user_id=? AND date(e.created_at, ?)>=? GROUP BY s.id''',(u,modifier,first_day)).fetchall()
            # Sessions created before active timing was introduced have no
            # trustworthy clock data. Fifteen seconds per recorded answer is a
            # deliberately conservative migration estimate, capped per round.
            duration=sum((row['active_seconds'] or min(1800,(row['answers'] or 0)*15)) for row in duration_rows)
        by_day={r['day']:dict(r) for r in rows}; days=[]
        for i in range(7):
            day=(now.date()-timedelta(days=6-i)).isoformat(); days.append({"date":day,"unique_words":by_day.get(day,{}).get('unique_words',0)})
        with self.conn() as c:
            unique=c.execute("SELECT COUNT(DISTINCT card_id) FROM study_events WHERE user_id=? AND date(created_at, ?)>=?",(u,modifier,first_day)).fetchone()[0]
            first=c.execute('''SELECT COUNT(*) FROM study_events e JOIN (
 SELECT card_id,MIN(id) first_id FROM study_events WHERE user_id=? AND date(created_at, ?)>=? GROUP BY card_id
) first_seen ON first_seen.first_id=e.id WHERE e.correct=1''',(u,modifier,first_day)).fetchone()[0]
        return {"trainings":trainings,"unique_words":unique,"first_correct":first,
                "accuracy":round(first*100/unique) if unique else 0,"new_learned":learned,
                "active_days":len(rows),"duration_seconds":duration,"days":days}
    def card_count(self,f):
        with self.conn() as c:return c.execute("SELECT count(*) FROM cards WHERE folder_id=?",(f,)).fetchone()[0]
    def due_count(self,u,f):
        with self.conn() as c:return c.execute("SELECT count(*) FROM cards c LEFT JOIN progress p ON p.card_id=c.id AND p.user_id=? WHERE c.folder_id=? AND (p.card_id IS NULL OR p.due_date IS NULL OR p.due_date<=?)",(u,f,date.today().isoformat())).fetchone()[0]
    def cards(self,f,offset=0,limit=PAGE_SIZE,query='',filter_mode='all',user_id=None):
        params=[user_id or 0,f]; where=''
        if query:
            where+=' AND (lower(c.term) LIKE lower(?) OR lower(c.translation) LIKE lower(?))'; params += [f'%{query}%',f'%{query}%']
        if filter_mode=='due': where+=' AND (p.card_id IS NULL OR p.due_date IS NULL OR p.due_date<=?)'; params.append(date.today().isoformat())
        elif filter_mode=='errors': where+=' AND EXISTS (SELECT 1 FROM study_events e WHERE e.user_id=? AND e.card_id=c.id AND e.correct=0 AND e.id=(SELECT MAX(e2.id) FROM study_events e2 WHERE e2.user_id=e.user_id AND e2.card_id=e.card_id))'; params.append(user_id or 0)
        params += [limit,offset]
        with self.conn() as c:return c.execute(f'''SELECT c.* FROM cards c
LEFT JOIN progress p ON p.card_id=c.id AND p.user_id=? WHERE c.folder_id=? {where}
ORDER BY c.id LIMIT ? OFFSET ?''',params).fetchall()
    def card(self,u,card_id):
        with self.conn() as c:return c.execute('''SELECT c.* FROM cards c WHERE c.id=? AND (
EXISTS(SELECT 1 FROM memberships m WHERE m.folder_id=c.folder_id AND m.user_id=?) OR
EXISTS(SELECT 1 FROM assignment_recipients ar JOIN assignments a ON a.id=ar.assignment_id WHERE ar.user_id=? AND a.folder_id=c.folder_id AND a.active=1))''',(card_id,u,u)).fetchone()
    def add_cards(self,u,f,items,topic_id=None):
        inserted=[]
        try:
            with self.conn() as c:
                explicit_topic=topic_id is not None
                if explicit_topic:
                    topic=c.execute("SELECT folder_id,(SELECT COUNT(*) FROM cards WHERE topic_id=?) n FROM topics WHERE id=?",(topic_id,topic_id)).fetchone()
                    if not topic or topic['folder_id']!=f:raise ValueError('invalid_topic')
                    if topic['n']+len(items)>MAX_CARDS_PER_TOPIC:raise ValueError('topic_word_limit')
                for item in items:
                    if not explicit_topic:
                        target=c.execute("SELECT t.id,COUNT(cd.id) n FROM topics t LEFT JOIN cards cd ON cd.topic_id=t.id WHERE t.folder_id=? GROUP BY t.id HAVING n<? ORDER BY t.is_system DESC,t.position,t.id LIMIT 1",(f,MAX_CARDS_PER_TOPIC)).fetchone()
                        if not target:
                            position=c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM topics WHERE folder_id=?",(f,)).fetchone()[0]
                            base='Без темы';name=base if position==0 else f'{base} {position+1}'
                            while c.execute("SELECT 1 FROM topics WHERE folder_id=? AND name=?",(f,name)).fetchone():position+=1;name=f'{base} {position+1}'
                            topic_id=c.execute("INSERT INTO topics(folder_id,name,is_system,position,created_by) VALUES(?,?,1,?,?)",(f,name,position,u)).lastrowid
                        else:topic_id=target['id']
                    term,tr=item[0],item[1]; extra=list(item[2:])+[None,None,None]
                    cur=c.execute("INSERT INTO cards(folder_id,topic_id,term,translation,transcription,example,example_translation,created_by) VALUES(?,?,?,?,?,?,?,?)",(f,topic_id,term,tr,extra[0],extra[1],extra[2],u));inserted.append(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            if 'topic_word_limit' in str(exc):raise ValueError('topic_word_limit') from exc
            raise
        return inserted
    def duplicate(self,f,term,tr):
        with self.conn() as c:return c.execute("SELECT id FROM cards WHERE folder_id=? AND lower(term)=lower(?) AND lower(translation)=lower(?)",(f,term,tr)).fetchone()
    def update_card(self,cid,field,value):
        if field not in {'term','translation','transcription','example','example_translation','audio_url','synonyms'}:raise ValueError('Unsupported card field')
        with self.conn() as c:c.execute(f"UPDATE cards SET {field}=? WHERE id=?",(value,cid))
    def delete_card(self,cid):
        with self.conn() as c:c.execute("DELETE FROM cards WHERE id=?",(cid,))
    def create_invite(self,u,f,role):
        token='folder_'+secrets.token_urlsafe(18)
        with self.conn() as c:c.execute("INSERT INTO invitations(token,folder_id,role,created_by) VALUES(?,?,?,?)",(token,f,role,u))
        return token
    def join(self,u,token):
        with self.conn() as c:
            r=c.execute("SELECT * FROM invitations WHERE token=? AND revoked=0",(token,)).fetchone()
            if not r:return None
            c.execute("INSERT INTO memberships VALUES(?,?,?) ON CONFLICT(folder_id,user_id) DO UPDATE SET role=CASE WHEN memberships.role='owner' THEN 'owner' ELSE excluded.role END",(r['folder_id'],u,r['role']))
            c.execute("INSERT INTO invite_claims(token,user_id,status) VALUES(?,?,'accepted') ON CONFLICT(token,user_id) DO UPDATE SET status='accepted',responded_at=CURRENT_TIMESTAMP",(token,u));return r['folder_id']
    def invite_preview(self,u,token):
        with self.conn() as c:return c.execute('''SELECT i.token,i.role,i.revoked,f.id folder_id,f.name folder_name,owner.name owner_name,
(SELECT status FROM invite_claims ic WHERE ic.token=i.token AND ic.user_id=?) response
FROM invitations i JOIN folders f ON f.id=i.folder_id JOIN users owner ON owner.telegram_id=f.owner_id WHERE i.token=?''',(u,token)).fetchone()
    def decline_invite(self,u,token):
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM invitations WHERE token=? AND revoked=0",(token,)).fetchone():return False
            c.execute("INSERT INTO invite_claims(token,user_id,status) VALUES(?,?,'declined') ON CONFLICT(token,user_id) DO UPDATE SET status='declined',responded_at=CURRENT_TIMESTAMP",(token,u));return True
    def members(self,f):
        with self.conn() as c:return c.execute("SELECT u.telegram_id,u.name,u.custom_avatar_key,u.telegram_avatar_url,m.role FROM memberships m JOIN users u ON u.telegram_id=m.user_id WHERE m.folder_id=?",(f,)).fetchall()
    def set_member_role(self,f,u,role):
        if role not in ('editor','member'):return False
        with self.conn() as c:
            cur=c.execute("UPDATE memberships SET role=? WHERE folder_id=? AND user_id=? AND role!='owner'",(role,f,u));return bool(cur.rowcount)
    def invites(self,f):
        with self.conn() as c:return c.execute("SELECT token,role,revoked,created_at FROM invitations WHERE folder_id=? ORDER BY created_at DESC",(f,)).fetchall()
    def revoke_invite(self,f,token):
        with self.conn() as c:
            cur=c.execute("UPDATE invitations SET revoked=1 WHERE folder_id=? AND token=?",(f,token));return bool(cur.rowcount)
    def revoke_invites(self,f):
        with self.conn() as c:c.execute("UPDATE invitations SET revoked=1 WHERE folder_id=?",(f,))
    def remove_member(self,f,u):
        with self.conn() as c:c.execute("DELETE FROM memberships WHERE folder_id=? AND user_id=? AND role!='owner'",(f,u))
    def delete_folder(self,f):
        with self.conn() as c:c.execute("DELETE FROM folders WHERE id=?",(f,))
    def rename(self,f,n):
        with self.conn() as c:c.execute("UPDATE folders SET name=? WHERE id=?",(n,f))
    def candidates(self,u,f,mode,topic_id=None):
        with self.conn() as c:
            q="SELECT c.id FROM cards c LEFT JOIN progress p ON p.card_id=c.id AND p.user_id=? WHERE c.folder_id=?"
            params=[u,f]
            if topic_id is not None:q+=" AND c.topic_id=?";params.append(topic_id)
            if mode.startswith('due'): q+=" AND (p.card_id IS NULL OR p.due_date IS NULL OR p.due_date<=?) ORDER BY RANDOM()"
            elif mode.startswith('errors'):
                q+=" AND EXISTS (SELECT 1 FROM study_events e WHERE e.user_id=? AND e.card_id=c.id AND e.correct=0 AND e.id=(SELECT MAX(e2.id) FROM study_events e2 WHERE e2.user_id=e.user_id AND e2.card_id=e.card_id)) ORDER BY RANDOM()";params.append(u);return [r[0] for r in c.execute(q,params).fetchall()]
            else:q+=" ORDER BY RANDOM()"
            if mode.startswith('due'):params.append(date.today().isoformat())
            return [r[0] for r in c.execute(q,params).fetchall()]
    def save_session(self,sid,u,f,mode,queue,study_format='cards',timezone_offset=0,topic_id=None,assignment_id=None):
        import json
        now=datetime.now(timezone.utc).isoformat()
        with self.conn() as c:c.execute("INSERT INTO sessions(id,user_id,folder_id,mode,queue,current_card,study_format,started_at,last_activity_at,timezone_offset,topic_id,assignment_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(sid,u,f,mode,json.dumps(queue),queue[0] if queue else None,study_format,now,now,max(-840,min(840,int(timezone_offset))),topic_id,assignment_id))
    def session(self,u,sid):
        with self.conn() as c:return c.execute("SELECT * FROM sessions WHERE id=? AND user_id=?",(sid,u)).fetchone()
    def answer(self,sid,card_id,success,mode,answer_text=None):
        import json
        with self.conn() as c:
            s=c.execute("SELECT * FROM sessions WHERE id=?",(sid,)).fetchone()
            if not s or s['answered'] or s['current_card']!=card_id:return None
            now=datetime.now(timezone.utc)
            active_delta=0
            if s['last_activity_at']:
                try:
                    previous_activity=datetime.fromisoformat(s['last_activity_at'].replace('Z','+00:00'))
                    if previous_activity.tzinfo is None: previous_activity=previous_activity.replace(tzinfo=timezone.utc)
                    active_delta=max(0,min(MAX_ACTIVE_GAP_SECONDS,round((now-previous_activity).total_seconds())))
                except (TypeError,ValueError):
                    active_delta=0
            errors=set(json.loads(s['errors'])); extras=json.loads(s['extra_counts']);
            previous=c.execute("SELECT COUNT(*) FROM study_events WHERE session_id=? AND card_id=?",(sid,card_id)).fetchone()[0]
            attempt=previous+1
            if not success: errors.add(card_id); extras[str(card_id)]=extras.get(str(card_id),0)+1
            # Every explicit answer updates this user's spaced-repetition state,
            # regardless of whether the session was started from Due or All.
            self._progress(c, s['user_id'], card_id, success, card_id in errors)
            c.execute("INSERT INTO study_events(session_id,user_id,card_id,attempt_no,correct,first_attempt,answer_text) VALUES(?,?,?,?,?,?,?)",(sid,s['user_id'],card_id,attempt,int(bool(success)),int(attempt==1 and bool(success)),answer_text))
            c.execute("UPDATE sessions SET answered=1,errors=?,extra_counts=?,active_seconds=active_seconds+?,last_activity_at=? WHERE id=?",(json.dumps(list(errors)),json.dumps(extras),active_delta,now.isoformat(),sid));return s
    def _progress(self,c,u,cid,success,had_error):
        p=c.execute("SELECT * FROM progress WHERE user_id=? AND card_id=?",(u,cid)).fetchone(); today=date.today()
        if not success:
            c.execute("INSERT INTO progress(user_id,card_id,level,due_date,last_success,error_count,mastery_status) VALUES(?,?,0,?,NULL,1,'learning') ON CONFLICT(user_id,card_id) DO UPDATE SET level=0,due_date=excluded.due_date,last_success=NULL,error_count=progress.error_count+1,mastery_score=MAX(0,progress.mastery_score-15),mastery_status='learning'",(u,cid,today.isoformat()));return
        level=0 if not p else p['level']; level=level if had_error else min(level+1,len(INTERVALS))
        days=1 if had_error else INTERVALS[max(level-1,0)]
        learned_at=datetime.now(timezone.utc).isoformat()
        c.execute("INSERT INTO progress(user_id,card_id,level,due_date,last_success,first_learned_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,card_id) DO UPDATE SET level=excluded.level,due_date=excluded.due_date,last_success=excluded.last_success,first_learned_at=COALESCE(progress.first_learned_at,excluded.first_learned_at)",(u,cid,level,(today+timedelta(days=days)).isoformat(),today.isoformat(),learned_at))
        c.execute("UPDATE progress SET success_count=success_count+1,distinct_days=(SELECT COUNT(DISTINCT date(created_at)) FROM study_events WHERE user_id=? AND card_id=?),mastery_score=MIN(100,mastery_score+?),mastery_status=CASE WHEN success_count+1>=5 AND distinct_days>=2 AND mastery_score+?>=80 THEN 'mastered' WHEN success_count+1>=2 THEN 'familiar' ELSE 'learning' END WHERE user_id=? AND card_id=?",(u,cid,20 if not had_error else 8,20 if not had_error else 8,u,cid))
    def advance(self,sid):
        import json
        with self.conn() as c:
            s=c.execute("SELECT * FROM sessions WHERE id=?",(sid,)).fetchone(); queue=json.loads(s['queue']); pos=s['pos']+1
            nxt=queue[pos] if pos<len(queue) else None
            c.execute("UPDATE sessions SET queue=?,pos=?,current_card=?,phase='ask',answered=0,last_activity_at=?,completed_at=CASE WHEN ? IS NULL THEN COALESCE(completed_at,?) ELSE completed_at END WHERE id=?",(json.dumps(queue),pos,nxt,datetime.now(timezone.utc).isoformat(),nxt,datetime.now(timezone.utc).isoformat(),sid));return (nxt,pos,len(queue),s)
    def stop_session(self,sid):
        with self.conn() as c:
            c.execute("UPDATE sessions SET current_card=NULL,answered=0,completed_at=COALESCE(completed_at,?) WHERE id=?",(datetime.now(timezone.utc).isoformat(),sid))
    def unfinished_session(self,u,f,topic_id=None):
        query="SELECT id FROM sessions WHERE user_id=? AND folder_id=? AND current_card IS NOT NULL AND completed_at IS NULL";params=[u,f]
        if topic_id is not None:query+=" AND topic_id=?";params.append(topic_id)
        query+=" ORDER BY created_at DESC LIMIT 1"
        with self.conn() as c:return c.execute(query,params).fetchone()


db=DB(os.getenv('DATABASE_PATH','data/slovo.db'))
from analytics import Analytics, normalize_ref, source_for_ref
analytics=Analytics(db)
router=Router(); dp=Dispatcher(); dp.include_router(router)
def esc(s): return html.escape(str(s))
def kb(*rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=d) for t,d in row] for row in rows])
def tr(u,key,**values):
    locale=db.locale(u)
    return TEXT.get(locale,TEXT['ru']).get(key,TEXT['ru'].get(key,key)).format(**values)
def language_rows(prefix,exclude=None,back='menu'):
    items=[(name,f'{prefix}:{code}') for code,name in LANGUAGES.items() if code!=exclude]
    rows=[tuple(items[i:i+2]) for i in range(0,len(items),2)]
    rows.append((("‹",back),))
    return rows
def app_button(u,payload=''):
    webapp_url=os.getenv('WEBAPP_URL','').strip()
    if webapp_url:
        separator='&' if '?' in webapp_url else '?'
        url=webapp_url+(f"{separator}start_param={payload}" if payload else '')
        return InlineKeyboardButton(text=tr(u,'open_app'),web_app=WebAppInfo(url=url))
    username=os.getenv('BOT_USERNAME','').lstrip('@')
    if username:
        return InlineKeyboardButton(text=tr(u,'open_app'),url=f'https://t.me/{username}?startapp')
    return InlineKeyboardButton(text=tr(u,'open_app'),callback_data='menu')
def MENU(u,payload=''):
    return InlineKeyboardMarkup(inline_keyboard=[
        [app_button(u,payload)],
        [InlineKeyboardButton(text=tr(u,'profile'),callback_data='profile')],
    ])
async def edit_or_send(event,text,markup=None):
    if isinstance(event,CallbackQuery):
        try: await event.message.edit_text(text,reply_markup=markup)
        except Exception: await event.message.answer(text,reply_markup=markup)
        await event.answer()
    else: await event.answer(text,reply_markup=markup)
async def menu(event,payload=''):
    u=event.from_user.id
    await edit_or_send(event,tr(u,'shell_welcome'),MENU(u,payload))
def can(role,edit=False):return role in ({"owner","editor"} if edit else ROLES)

@router.message(CommandStart())
async def start(m:Message, command:CommandObject):
    raw_arg=(command.args or '').strip()
    referral='organic' if raw_arg.startswith(('inv_','folder_','class_','ref_')) else normalize_ref(raw_arg)
    created=db.user(m.from_user.id,m.from_user.full_name,ref_code=referral,acquisition_source=source_for_ref(referral))
    if created: analytics.safe_track(m.from_user.id,'registration_completed',idempotency_key=f'registration:{m.from_user.id}')
    if created and raw_arg.startswith('ref_'):db.apply_referral(m.from_user.id,raw_arg[4:])
    await menu(m,raw_arg if raw_arg.startswith(('inv_','folder_','class_')) else '')
@router.message(Command('menu'))
async def cmd_menu(m):db.clear_draft(m.from_user.id);await menu(m)
@router.message(Command('cancel'))
async def cancel(m):db.clear_draft(m.from_user.id);await menu(m)

async def show_folders(e,u=None,page=0,action='open'):
    u=u or e.from_user.id; fs=db.folders(u); chunk=fs[page*PAGE_SIZE:(page+1)*PAGE_SIZE]; rows=[]
    for f in chunk: rows.append(((('👥 ' if f['role']!='owner' else '')+f['name'],f'{action}:{f["id"]}'),))
    nav=[]
    if page:nav.append(('‹',f'folders:{page-1}'))
    if (page+1)*PAGE_SIZE<len(fs):nav.append(('›',f'folders:{page+1}'))
    if nav:rows.append(tuple(nav))
    rows += [((tr(u,'new_folder'),"folder:new"),),((tr(u,'back'),"menu"),)]
    await edit_or_send(e,f"<b>{tr(u,'folders')}</b>" if fs else tr(u,'no_folders'),kb(*rows))
async def show_folder(e,f):
    u=e.from_user.id;x=db.folder(u,f)
    if not x:return await edit_or_send(e,tr(u,'folder_unavailable'),MENU(u))
    role=x['role']; rows=[((tr(u,'study'),f"learn:{f}"),(tr(u,'add'),f"add:{f}")),((tr(u,'word_list'),f"words:{f}:0"),)]
    if role=='owner':rows.append(((tr(u,'invite'),f"invite:{f}"),))
    if role=='owner':rows.append(((tr(u,'settings'),f"settings:{f}"),))
    rows.append(((tr(u,'back'),"folders:0"),))
    pair=f"{LANGUAGES[x['source_lang']]} → {LANGUAGES[x['target_lang']]}"
    await edit_or_send(e,f"📁 <b>{esc(x['name'])}</b>\n{pair}\n{tr(u,'words')}: {db.card_count(f)}\n{tr(u,'due')}: {db.due_count(u,f)}",kb(*rows))

@router.callback_query(F.data=='menu')
async def cb_menu(q):await menu(q)
@router.callback_query(F.data=='ui_lang')
async def ui_lang(q):
 await edit_or_send(q,tr(q.from_user.id,'choose_interface'),kb(*language_rows('setlocale')))
@router.callback_query(F.data.startswith('setlocale:'))
async def setlocale(q):
 code=q.data.split(':')[1]
 if code not in LANGUAGES:return await q.answer()
 db.set_locale(q.from_user.id,code);await menu(q)
@router.callback_query(F.data=='profile')
async def profile(q):
 await show_profile(q)
async def show_profile(event):
 u=event.from_user.id;s=db.profile_stats(u);loc=db.locale(u)
 text=(f"{tr(u,'profile_title')}\n\n<b>{esc(event.from_user.full_name)}</b>\n"
       f"{tr(u,'interface_lang')}: {LANGUAGES[loc]}\n"
       f"{tr(u,'folders_count')}: {s['folders']}\n"
       f"{tr(u,'words_count')}: {s['words']}\n"
       f"{tr(u,'learned_count')}: {s['learned']}\n"
       f"{tr(u,'sessions_count')}: {s['sessions']}\n\n"
       f"{tr(u,'profile_hint')}")
 markup=InlineKeyboardMarkup(inline_keyboard=[[app_button(u)],[InlineKeyboardButton(text=tr(u,'back'),callback_data='menu')]])
 await edit_or_send(event,text,markup)
@router.message(Command('profile'))
async def cmd_profile(m):
 db.user(m.from_user.id,m.from_user.full_name);await show_profile(m)

# Old inline keyboards may still exist in users' chats. Any obsolete action now
# returns to the lightweight Mini App launcher instead of opening legacy flows.
@router.callback_query()
async def legacy_callback(q):
 db.clear_draft(q.from_user.id);await menu(q)
@router.callback_query(F.data.startswith('folders:'))
async def cb_folders(q):await show_folders(q,page=int(q.data.split(':')[1]))
@router.callback_query(F.data.startswith('open:'))
async def cb_open(q):await show_folder(q,int(q.data.split(':')[1]))
@router.callback_query(F.data=='folder:new')
async def folder_new(q):
 u=q.from_user.id;db.set_draft(u,'folder','');await edit_or_send(q,tr(u,'folder_name'),kb(((tr(u,'cancel'),"menu"),)))
@router.callback_query(F.data.startswith('folderlang:'))
async def folderlang(q):
 import json
 _,stage,code=q.data.split(':');u=q.from_user.id;d=db.draft(u)
 if code not in LANGUAGES or not d:return await q.answer(tr(u,'session_gone'),show_alert=True)
 if stage=='source' and d['kind']=='folder_source':
  db.set_draft(u,'folder_target',json.dumps({'name':d['payload'],'source':code}))
  return await edit_or_send(q,tr(u,'target_lang'),kb(*language_rows('folderlang:target',exclude=code)))
 if stage=='target' and d['kind']=='folder_target':
  data=json.loads(d['payload']);f=db.create_folder(u,data['name'],data['source'],code);db.clear_draft(u);return await show_folder(q,f)
 await q.answer(tr(u,'session_gone'),show_alert=True)
@router.callback_query(F.data.startswith('settings:'))
async def settings(q):
 f=int(q.data.split(':')[1]); x=db.folder(q.from_user.id,f)
 if not x or x['role']!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 u=q.from_user.id
 await edit_or_send(q,f"<b>{tr(u,'folder_settings')}</b>",kb(((tr(u,'folder_languages'),f"flangs:{f}"),),((tr(u,'rename'),f"rename:{f}"),),((tr(u,'members'),f'members:{f}'),),((tr(u,'delete_folder'),f'delfolder:{f}'),),((tr(u,'back'),f'open:{f}'),)))
@router.callback_query(F.data.startswith('flangs:'))
async def flangs(q):
 f=int(q.data.split(':')[1]);u=q.from_user.id;x=db.folder(u,f)
 if not x or x['role']!='owner':return await q.answer(tr(u,'no_access'),show_alert=True)
 db.set_draft(u,'flang_source',str(f));await edit_or_send(q,tr(u,'edit_source'),kb(*language_rows('setflang:source',back=f'settings:{f}')))
@router.callback_query(F.data.startswith('setflang:'))
async def setflang(q):
 import json
 _,stage,code=q.data.split(':');u=q.from_user.id;d=db.draft(u)
 if code not in LANGUAGES or not d:return await q.answer(tr(u,'session_gone'),show_alert=True)
 if stage=='source' and d['kind']=='flang_source':
  f=int(d['payload']);db.set_draft(u,'flang_target',json.dumps({'f':f,'source':code}))
  return await edit_or_send(q,tr(u,'edit_target'),kb(*language_rows('setflang:target',exclude=code,back=f'settings:{f}')))
 if stage=='target' and d['kind']=='flang_target':
  data=json.loads(d['payload'])
  if db.role(u,data['f'])!='owner':return await q.answer(tr(u,'no_access'),show_alert=True)
  db.set_folder_languages(data['f'],data['source'],code);db.clear_draft(u);await q.answer(tr(u,'languages_saved'));return await show_folder(q,data['f'])
 await q.answer(tr(u,'session_gone'),show_alert=True)
@router.callback_query(F.data.startswith('rename:'))
async def rename(q):
 f=int(q.data.split(':')[1]);u=q.from_user.id;db.set_draft(u,'rename',str(f));await edit_or_send(q,tr(u,'new_name'),kb(((tr(u,'cancel'),f"settings:{f}"),)))
@router.callback_query(F.data.startswith('delfolder:'))
async def delf(q):
 f=int(q.data.split(':')[1]);u=q.from_user.id;await edit_or_send(q,tr(u,'delete_folder_confirm'),kb(((tr(u,'delete'),f"delfolderok:{f}"),(tr(u,'back'),f"settings:{f}"))))
@router.callback_query(F.data.startswith('delfolderok:'))
async def delfok(q):
 f=int(q.data.split(':')[1]); x=db.folder(q.from_user.id,f)
 if not x or x['role']!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 db.delete_folder(f);await show_folders(q)

@router.callback_query(F.data.startswith('invite:'))
async def invite(q):
 f=int(q.data.split(':')[1]); x=db.folder(q.from_user.id,f)
 if not x or x['role']!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 u=q.from_user.id;await edit_or_send(q,tr(u,'invite_rights'),kb(((tr(u,'editor_right'),f"inviteok:{f}:editor"),),((tr(u,'member_right'),f'inviteok:{f}:member'),),((tr(u,'back'),f'open:{f}'),)))
@router.callback_query(F.data.startswith('inviteok:'))
async def inviteok(q):
 _,f,role=q.data.split(':'); f=int(f)
 if db.role(q.from_user.id,f)!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 token=db.create_invite(q.from_user.id,f,role)
 me=await q.bot.get_me()
 u=q.from_user.id;await edit_or_send(q,f"{tr(u,'link_ready')}\n<code>https://t.me/{me.username}?start={token}</code>",kb(((tr(u,'back'),f"open:{f}"),)))
@router.callback_query(F.data.startswith('members:'))
async def members(q):
 f=int(q.data.split(':')[1]); x=db.folder(q.from_user.id,f)
 if not x or x['role']!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 rows=[]
 for m in db.members(f):
  label=f"{m['name'] or m['telegram_id']} — {ROLE_LABELS[db.locale(q.from_user.id)][m['role']]}"
  rows.append(((label, 'noop'),) if m['role']=='owner' else ((label,f"member:{f}:{m['telegram_id']}"),))
 u=q.from_user.id;rows += [((tr(u,'revoke'),f"revoke:{f}"),),((tr(u,'back'),f'settings:{f}'),)]
 await edit_or_send(q,f"<b>{tr(u,'members')}</b>",kb(*rows))
@router.callback_query(F.data.startswith('member:'))
async def member(q):
 _,f,u=q.data.split(':');f=int(f)
 if db.role(q.from_user.id,f)!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 me=q.from_user.id;await edit_or_send(q,tr(me,'remove_member'),kb(((tr(me,'delete'),f"remove:{f}:{u}"),(tr(me,'back'),f"members:{f}"))))
@router.callback_query(F.data.startswith('remove:'))
async def remove(q):
 _,f,u=q.data.split(':');f=int(f)
 if db.role(q.from_user.id,f)!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 db.remove_member(f,int(u));await members(q)
@router.callback_query(F.data.startswith('revoke:'))
async def revoke(q):
 f=int(q.data.split(':')[1])
 if db.role(q.from_user.id,f)!='owner':return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 db.revoke_invites(f);await q.answer(tr(q.from_user.id,'revoked'));await members(q)
@router.callback_query(F.data=='noop')
async def noop(q):await q.answer()

def parse_words(text):
    good=[]; bad=[]
    for raw in text.splitlines():
        raw=raw.strip()
        if not raw:continue
        parts=raw.split('\t',1) if '\t' in raw else raw.split(' — ',1)
        if len(parts)==2 and all(p.strip() for p in parts):good.append((parts[0].strip(),parts[1].strip()))
        elif len(text.splitlines())==1 and raw:good.append((raw,None))
        else:bad.append(raw)
    return good,bad
async def choose_folder(e,kind,payload):
    u=e.from_user.id;db.set_draft(u,kind,payload); fs=db.folders(u)
    if not fs:return await edit_or_send(e,tr(u,'create_first'),kb(((tr(u,'new_folder'),"folder:new"),),((tr(u,'cancel'),'menu'),)))
    rows=[((f['name'],f'pick:{f["id"]}'),) for f in fs]+[((tr(u,'cancel'),"menu"),)]
    await edit_or_send(e,tr(u,'choose_folder'),kb(*rows))
@router.callback_query(F.data=='add:choose')
async def add_choose(q):await choose_folder(q,'add','')
@router.callback_query(F.data.startswith('add:'))
async def add(q):
 f=int(q.data.split(':')[1]);u=q.from_user.id
 if not can(db.role(u,f),True):return await q.answer(tr(u,'no_access'),show_alert=True)
 db.set_draft(u,'add',str(f));await edit_or_send(q,tr(u,'send_words'),kb(((tr(u,'cancel'),f"open:{f}"),)))
@router.callback_query(F.data.startswith('pick:'))
async def pick(q):
 f=int(q.data.split(':')[1]);d=db.draft(q.from_user.id)
 if not d:return await q.answer(tr(q.from_user.id,'flow_expired'),show_alert=True)
 if d['kind']=='add':
  u=q.from_user.id;db.set_draft(u,'add',str(f));await edit_or_send(q,tr(u,'send_words'),kb(((tr(u,'cancel'),f"open:{f}"),)))
 elif d['kind']=='pending':await preview(q,f,d['payload'])
async def preview(e,f,encoded):
    import json
    items=json.loads(encoded); duplicates=[x for x in items if db.duplicate(f,*x)]
    if db.card_count(f)+len(items)>MAX_CARDS_PER_FOLDER:
        return await edit_or_send(e,tr(e.from_user.id,'folder_word_limit'),kb(((tr(e.from_user.id,'to_folder'),f"open:{f}"),)))
    if duplicates:
        db.set_draft(e.from_user.id,'dupes',json.dumps({'f':f,'items':items}))
        u=e.from_user.id;return await edit_or_send(e,tr(u,'duplicates',count=len(duplicates)),kb(((tr(u,'skip'),'dupes:skip'),),((tr(u,'add_separate'),'dupes:add'),),((tr(u,'cancel'),'menu'),)))
    db.set_draft(e.from_user.id,'ready',json.dumps({'f':f,'items':items}))
    u=e.from_user.id;x=db.folder(u,f);await edit_or_send(e,tr(u,'add_confirm',count=len(items),name=esc(x['name'])),kb(((tr(u,'save'),'save'),),((tr(u,'change_folder'),'changefolder'),),((tr(u,'cancel'),'menu'),)))
@router.callback_query(F.data.startswith('dupes:'))
async def dupes(q):
 d=db.draft(q.from_user.id)
 if not d or d['kind']!='dupes':return await q.answer(tr(q.from_user.id,'flow_expired'),show_alert=True)
 import json;x=json.loads(d['payload']); items=x['items'] if q.data.endswith('add') else [i for i in x['items'] if not db.duplicate(x['f'],*i)]
 await preview(q,x['f'],json.dumps(items))
@router.callback_query(F.data=='changefolder')
async def changefolder(q):
 d=db.draft(q.from_user.id)
 if d and d['kind']=='ready':await choose_folder(q,'pending',d['payload'])
@router.callback_query(F.data=='save')
async def save(q):
 d=db.draft(q.from_user.id)
 if not d or d['kind']!='ready':return await q.answer(tr(q.from_user.id,'flow_expired'),show_alert=True)
 import json;x=json.loads(d['payload']);f=x['f']
 if not can(db.role(q.from_user.id,f),True):return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 db.add_cards(q.from_user.id,f,x['items']);db.clear_draft(q.from_user.id); name=db.folder(q.from_user.id,f)['name']
 u=q.from_user.id;await edit_or_send(q,tr(u,'added',count=len(x['items'])),kb(((tr(u,'add_more'),f"add:{f}"),(tr(u,'study'),f"learn:{f}")),((tr(u,'to_folder'),f"open:{f}"),)))

@router.callback_query(F.data.startswith('words:'))
async def words(q):
 _,f,p=q.data.split(':');f=int(f);p=int(p)
 if not db.folder(q.from_user.id,f):return await q.answer(tr(q.from_user.id,'folder_unavailable'),show_alert=True)
 cards=db.cards(f,p*PAGE_SIZE); rows=[((f"{c['term']} — {c['translation']}",f"card:{c['id']}:{p}"),) for c in cards];nav=[]
 if p:nav.append(('‹',f'words:{f}:{p-1}'))
 if len(cards)==PAGE_SIZE:nav.append(('›',f'words:{f}:{p+1}'))
 if nav:rows.append(tuple(nav))
 u=q.from_user.id;rows.append(((tr(u,'back'),f"open:{f}"),));await edit_or_send(q,f"<b>{tr(u,'word_list')}</b>",kb(*rows))
@router.callback_query(F.data.startswith('card:'))
async def card(q):
 _,cid,p=q.data.split(':');c=db.card(q.from_user.id,int(cid))
 if not c:return await q.answer(tr(q.from_user.id,'card_deleted'),show_alert=True)
 role=db.role(q.from_user.id,c['folder_id']);rows=[]
 u=q.from_user.id
 if can(role,True):rows += [((tr(u,'edit_word'),f"editterm:{cid}"),(tr(u,'edit_translation'),f"edittr:{cid}")),((tr(u,'delete'),f"delcard:{cid}"),)]
 rows.append(((tr(u,'back'),f"words:{c['folder_id']}:{p}"),));await edit_or_send(q,f"<b>{esc(c['term'])}</b>\n{esc(c['translation'])}",kb(*rows))
@router.callback_query(F.data.startswith('editterm:')|F.data.startswith('edittr:'))
async def editcard(q):
 kind,cid=q.data.split(':');c=db.card(q.from_user.id,int(cid))
 if not c or not can(db.role(q.from_user.id,c['folder_id']),True):return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 u=q.from_user.id;db.set_draft(u,kind,cid);await edit_or_send(q,tr(u,'new_value'),kb(((tr(u,'cancel'),f"card:{cid}:0"),)))
@router.callback_query(F.data.startswith('delcard:'))
async def delcard(q):
 cid=int(q.data.split(':')[1]);c=db.card(q.from_user.id,cid)
 if not c or not can(db.role(q.from_user.id,c['folder_id']),True):return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 u=q.from_user.id;await edit_or_send(q,tr(u,'delete_card'),kb(((tr(u,'delete'),f"delcardok:{cid}"),(tr(u,'back'),f"card:{cid}:0"))))
@router.callback_query(F.data.startswith('delcardok:'))
async def delcardok(q):
 cid=int(q.data.split(':')[1]);c=db.card(q.from_user.id,cid)
 if not c or not can(db.role(q.from_user.id,c['folder_id']),True):return await q.answer(tr(q.from_user.id,'no_access'),show_alert=True)
 f=c['folder_id'];db.delete_card(cid);await show_folder(q,f)

@router.callback_query(F.data=='learn:choose')
async def learn_choose(q):
 u=q.from_user.id;fs=db.folders(u); rows=[((f['name'],f'learn:{f["id"]}'),) for f in fs]+[((tr(u,'back'),"menu"),)]
 await edit_or_send(q,tr(u,'choose_folder'),kb(*rows))
@router.callback_query(F.data.startswith('learn:'))
async def learn(q):
 f=int(q.data.split(':')[1]);u=q.from_user.id;x=db.folder(u,f)
 if not x:return await q.answer(tr(u,'folder_unavailable'),show_alert=True)
 forward=f"{LANGUAGES[x['source_lang']]} → {LANGUAGES[x['target_lang']]}"
 reverse=f"{LANGUAGES[x['target_lang']]} → {LANGUAGES[x['source_lang']]}"
 await edit_or_send(q,tr(u,'choose_study'),kb(((forward,f"direction:{f}:fwd"),),((reverse,f"direction:{f}:rev"),),((tr(u,'back'),f'open:{f}'),)))
@router.callback_query(F.data.startswith('direction:'))
async def direction(q):
 _,f,direction=q.data.split(':');f=int(f);u=q.from_user.id
 if not db.folder(u,f):return await q.answer(tr(u,'folder_unavailable'),show_alert=True)
 await edit_or_send(q,tr(u,'choose_mode'),kb(((tr(u,'due_mode'),f"mode:{f}:due:{direction}"),),((tr(u,'all_mode'),f'mode:{f}:all:{direction}'),),((tr(u,'back'),f'learn:{f}'),)))
@router.callback_query(F.data.startswith('mode:'))
async def mode(q):
 parts=q.data.split(':');_,f,kind=parts[:3];direction=parts[3] if len(parts)>3 else 'fwd';f=int(f);u=q.from_user.id
 session_mode=f'{kind}:{direction}';ids=db.candidates(u,f,session_mode)
 if not ids:return await edit_or_send(q,tr(u,'nothing_due'),kb(((tr(u,'all_mode'),f"mode:{f}:all:{direction}"),),((tr(u,'add'),f'add:{f}'),),((tr(u,'to_folder'),f'open:{f}'),)))
 ids=ids[:10];sid=secrets.token_urlsafe(9);db.save_session(sid,u,f,session_mode,ids);await show_question(q,sid)
async def show_question(e,sid):
 u=e.from_user.id;s=db.session(u,sid)
 if not s:return await edit_or_send(e,tr(u,'session_gone'),MENU(u))
 c=db.card(e.from_user.id,s['current_card'])
 if not c:return await next_card(e,sid)
 front=c['translation'] if s['mode'].endswith(':rev') else c['term']
 total=min(10,len(set(__import__('json').loads(s['queue']))))
 await edit_or_send(e,f"{tr(u,'question',pos=s['pos']+1,total=total)}\n\n<b>{esc(front)}</b>",kb(((tr(u,'know'),f"answer:{sid}:{c['id']}:know"),(tr(u,'dont_know'),f"answer:{sid}:{c['id']}:no")),((tr(u,'finish'),f"finish:{sid}"),)))
@router.callback_query(F.data.startswith('answer:'))
async def answer(q):
 _,sid,cid,result=q.data.split(':');cid=int(cid);u=q.from_user.id;s=db.session(u,sid)
 if not s or s['current_card']!=cid:return await q.answer(tr(u,'stale_button'),show_alert=True)
 c=db.card(q.from_user.id,cid)
 if not c:return await q.answer(tr(u,'card_deleted'),show_alert=True)
 front=c['translation'] if s['mode'].endswith(':rev') else c['term'];back=c['term'] if s['mode'].endswith(':rev') else c['translation']
 if result=='no':
  if not db.answer(sid,cid,False,s['mode']):return await q.answer(tr(u,'answered'),show_alert=True)
  await edit_or_send(q,f"<b>{esc(front)}</b>\n{esc(back)}",kb(((tr(u,'next'),f"next:{sid}"),)))
 else:
  # Marking happens only after explicit confirmation.
  await edit_or_send(q,f"<b>{esc(front)}</b>\n{esc(back)}",kb(((tr(u,'correct_next'),f"confirm:{sid}:{cid}"),(tr(u,'mistake'),f"answer:{sid}:{cid}:no")),))
@router.callback_query(F.data.startswith('confirm:'))
async def confirm(q):
 _,sid,cid=q.data.split(':');cid=int(cid);u=q.from_user.id;s=db.session(u,sid)
 if not s or s['current_card']!=cid:return await q.answer(tr(u,'stale_button'),show_alert=True)
 if not db.answer(sid,cid,True,s['mode']):return await q.answer(tr(u,'answered'),show_alert=True)
 await next_card(q,sid)
@router.callback_query(F.data.startswith('next:'))
async def next_(q):await next_card(q,q.data.split(':')[1])
async def next_card(e,sid):
 u=e.from_user.id;s=db.session(u,sid)
 if not s:return await edit_or_send(e,tr(u,'session_gone'),MENU(u))
 if not s['answered']:return await e.answer(tr(u,'answer_first'),show_alert=True)
 nxt,pos,total,old=db.advance(sid)
 if nxt:return await show_question(e,sid)
 await finish(e,sid)
@router.callback_query(F.data.startswith('finish:'))
async def finishcb(q):
 sid=q.data.split(':')[1];db.stop_session(sid);await finish(q,sid)
async def finish(e,sid):
 import json
 s=db.session(e.from_user.id,sid)
 u=e.from_user.id
 if not s:return await edit_or_send(e,tr(u,'session_gone'),MENU(u))
 errors=set(json.loads(s['errors'])); unique=min(10,len(set(json.loads(s['queue']))));f=s['folder_id']
 direction='rev' if s['mode'].endswith(':rev') else 'fwd'
 rows=[]
 if errors:rows.append(((tr(u,'repeat_errors'),f"errors:{sid}"),))
 if len(db.candidates(u,f,'all'))>unique:rows.append(((tr(u,'more'),f"mode:{f}:all:{direction}"),))
 rows.append(((tr(u,'to_folder'),f"open:{f}"),))
 await edit_or_send(e,tr(u,'done',total=unique,ok=max(0,unique-len(errors)),bad=len(errors)),kb(*rows))
@router.callback_query(F.data.startswith('errors:'))
async def errors(q):
 sid=q.data.split(':')[1];u=q.from_user.id;s=db.session(u,sid)
 if not s:return await q.answer(tr(u,'session_gone'),show_alert=True)
 import json;ids=list(json.loads(s['errors']))
 if not ids:return await q.answer(tr(u,'no_errors'),show_alert=True)
 direction='rev' if s['mode'].endswith(':rev') else 'fwd';ns=secrets.token_urlsafe(9);db.save_session(ns,q.from_user.id,s['folder_id'],f'due:{direction}',ids[:10]);await show_question(q,ns)

@router.message(F.text)
async def input_text(m:Message):
    if m.text.startswith('/'):return
    db.user(m.from_user.id,m.from_user.full_name)
    db.clear_draft(m.from_user.id)
    await menu(m)

async def main():
    token=os.getenv('BOT_TOKEN')
    if not token:raise RuntimeError('Set BOT_TOKEN in .env or environment')
    bot=Bot(token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types())
if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
