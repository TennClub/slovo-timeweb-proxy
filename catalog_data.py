"""Built-in Slovo catalogue and idempotent seed routine."""
from __future__ import annotations

import argparse
import os
import sqlite3

from dotenv import load_dotenv

SYSTEM_USER_ID = -70500

CATEGORIES = (
    ("everyday", "На каждый день", "home", 10),
    ("travel", "Путешествия", "plane", 20),
    ("communication", "Общение и отношения", "messages", 30),
    ("work", "Работа и бизнес", "briefcase", 40),
    ("interests", "Спорт и увлечения", "activity", 50),
)

SETS = (
    ("introductions", "everyday", "Знакомство", "Представься и узнай собеседника", "user-round", 10),
    ("daily-routine", "everyday", "Мой день", "Расскажи о своём обычном дне", "sun", 20),
    ("shopping", "everyday", "Покупки", "Выбери товар, уточни цену и оплати покупку", "shopping-bag", 30),
    ("restaurant", "travel", "В ресторане", "Забронируй столик и сделай заказ", "utensils", 10),
    ("airport", "travel", "В аэропорту", "Регистрация, багаж и посадка", "plane", 20),
    ("hotel", "travel", "В отеле", "Заселение, удобства и просьбы персоналу", "hotel", 30),
    ("emotions", "communication", "Эмоции", "Расскажи, что чувствуешь", "heart", 10),
    ("friendship", "communication", "Дружба", "Знакомства, поддержка и отношения с друзьями", "users", 20),
    ("job-interview", "work", "Собеседование", "Расскажи об опыте и обсуди работу", "briefcase", 10),
    ("work-emails", "work", "Рабочая переписка", "Пиши понятные деловые письма", "mail", 20),
    ("workouts", "interests", "Тренировки", "Упражнения, техника и восстановление", "dumbbell", 10),
    ("films", "interests", "Кино и сериалы", "Обсуди сюжет, актёров и впечатления", "clapperboard", 20),
)

RAW_CARDS = {
"introductions": """first name | имя
last name | фамилия
nickname | прозвище
introduce yourself | представиться
Nice to meet you | Приятно познакомиться
Where are you from? | Откуда ты?
hometown | родной город
country | страна
nationality | национальность
native language | родной язык
foreign language | иностранный язык
age | возраст
birthday | день рождения
occupation | род занятий
student | учащийся, студент
classmate | одноклассник, одногруппник
colleague | коллега
neighbour | сосед
hobby | увлечение
be interested in | интересоваться чем-либо
live | жить
grow up | расти, взрослеть
move to | переехать в
What do you do? | Чем ты занимаешься?
keep in touch | поддерживать связь""",
"daily-routine": """wake up | просыпаться
get up | вставать с постели
make the bed | заправлять кровать
brush your teeth | чистить зубы
take a shower | принимать душ
get dressed | одеваться
have breakfast | завтракать
leave home | выходить из дома
go to school | идти в школу
get to work | добираться до работы
take the bus | ехать на автобусе
on time | вовремя
be late | опаздывать
schedule | расписание
daily routine | распорядок дня
have lunch | обедать
take a break | делать перерыв
do homework | делать домашнее задание
finish work | заканчивать работу
get home | возвращаться домой
cook dinner | готовить ужин
do the dishes | мыть посуду
relax | отдыхать, расслабляться
go to bed | ложиться спать
fall asleep | засыпать""",
"shopping": """shop | магазин
shopping list | список покупок
shopping basket | корзина для покупок
shopping trolley | тележка для покупок
shelf | полка
price | цена
price tag | ценник
discount | скидка
sale | распродажа
special offer | специальное предложение
cheap | дешёвый
expensive | дорогой
affordable | доступный по цене
size | размер
try on | примерять
fitting room | примерочная
fit | подходить по размеру
in stock | в наличии
out of stock | нет в наличии
checkout | касса
cashier | кассир
pay by card | платить картой
receipt | чек
return an item | вернуть товар
refund | возврат денег""",
"restaurant": """book a table | забронировать столик
a table for two | столик на двоих
menu | меню
waiter | официант
order | заказывать
starter | закуска перед основным блюдом
main course | основное блюдо
side dish | гарнир
dessert | десерт
drink | напиток
tap water | водопроводная вода
still water | негазированная вода
sparkling water | газированная вода
vegetarian | вегетарианский
ingredients | ингредиенты
food allergy | пищевая аллергия
spicy | острый
well done | хорошо прожаренный
medium rare | слабой прожарки
What would you recommend? | Что вы посоветуете?
Could we have the bill, please? | Можно счёт, пожалуйста?
service charge | плата за обслуживание
tip | чаевые
split the bill | разделить счёт
takeaway | еда навынос""",
"airport": """airport | аэропорт
terminal | терминал
departure | вылет
arrival | прилёт
flight | рейс
airline | авиакомпания
passport | паспорт
boarding pass | посадочный талон
check-in desk | стойка регистрации
check in | зарегистрироваться на рейс
luggage | багаж
hand luggage | ручная кладь
checked baggage | сдаваемый багаж
baggage allowance | норма провоза багажа
security check | досмотр безопасности
passport control | паспортный контроль
gate | выход на посадку
boarding | посадка на самолёт
take off | взлетать
land | приземляться
delayed | задержанный
cancelled | отменённый
connecting flight | стыковочный рейс
baggage reclaim | зона выдачи багажа
customs | таможня""",
"hotel": """reservation | бронирование
reception | стойка регистрации
guest | гость
check-in | заселение
check-out | выезд из отеля
single room | одноместный номер
double room | номер с одной двуспальной кроватью
twin room | номер с двумя отдельными кроватями
key card | ключ-карта
floor | этаж
lift | лифт
breakfast included | завтрак включён
room service | обслуживание в номере
housekeeping | уборка номеров
towel | полотенце
bed linen | постельное бельё
air conditioning | кондиционер, кондиционирование воздуха
Wi-Fi password | пароль от Wi-Fi
safe | сейф
available | свободный, доступный
fully booked | все номера забронированы
late checkout | поздний выезд
luggage storage | хранение багажа
Could I change rooms? | Можно поменять номер?
The air conditioning isn’t working | Кондиционер не работает""",
"emotions": """happy | счастливый, радостный
sad | грустный
excited | радостно взволнованный
worried | обеспокоенный
nervous | нервничающий
calm | спокойный
relaxed | расслабленный
angry | злой, сердитый
annoyed | раздражённый
upset | расстроенный
disappointed | разочарованный
surprised | удивлённый
confused | растерянный, сбитый с толку
embarrassed | смущённый
proud | гордый
grateful | благодарный
hopeful | полный надежды
lonely | одинокий
bored | скучающий
frustrated | раздосадованный
overwhelmed | перегруженный делами или эмоциями
relieved | испытывающий облегчение
jealous | ревнующий
content | довольный, удовлетворённый
apprehensive | тревожащийся в ожидании чего-либо""",
"friendship": """friendship | дружба
close friend | близкий друг
best friend | лучший друг
acquaintance | знакомый
get to know someone | узнавать человека ближе
make friends | заводить друзей
have something in common | иметь что-то общее
get along with | ладить с
hang out | проводить время вместе
meet up | встречаться
invite someone over | приглашать кого-либо к себе
join | присоединяться
trust | доверять
rely on | полагаться на
support | поддерживать
care about | заботиться о, дорожить
listen to | слушать
give advice | давать советы
cheer someone up | подбадривать кого-либо
let someone down | подводить кого-либо
argue | спорить, ссориться
fall out with | поссориться с
make up with | помириться с
apologise | извиняться
lose touch | терять связь""",
"job-interview": """job interview | собеседование
apply for a job | откликаться на вакансию
vacancy | вакансия
CV | резюме
cover letter | сопроводительное письмо
candidate | кандидат
employer | работодатель
recruiter | специалист по подбору персонала
work experience | опыт работы
qualification | квалификация
skill | навык
strength | сильная сторона
weakness | слабая сторона
achievement | достижение
responsibility | обязанность, ответственность
salary | зарплата
working hours | рабочие часы
full-time | на полный рабочий день
part-time | на неполный рабочий день
remote work | удалённая работа
teamwork | командная работа
problem-solving | решение проблем
Tell me about yourself | Расскажите о себе
Why should we hire you? | Почему нам стоит вас нанять?
When can you start? | Когда вы можете приступить к работе?""",
"work-emails": """subject line | тема письма
recipient | получатель
attachment | вложение
attach a file | прикрепить файл
forward an email | переслать письмо
reply | отвечать
reply all | ответить всем
Dear colleagues | Уважаемые коллеги
I’m writing to ask about | Пишу, чтобы узнать о
Please find attached | Во вложении вы найдёте
Could you please clarify? | Не могли бы вы уточнить?
Let me know | Дайте мне знать
confirm | подтверждать
confirmation | подтверждение
request | запрос, просьба
deadline | крайний срок
schedule a meeting | назначить встречу
reschedule | перенести на другое время
availability | доступное время
update | новая информация о ходе дел
follow up on | повторно обратиться по поводу
Thank you for your reply | Спасибо за ответ
Sorry for the delay | Извините за задержку
I look forward to hearing from you | Буду ждать вашего ответа
Best regards | С уважением""",
"workouts": """workout | тренировка
warm up | разминаться
cool down | выполнять заминку
stretch | растягивать мышцы
exercise | упражнение
training plan | план тренировок
coach | тренер
teammate | товарищ по команде
equipment | спортивный инвентарь
strength | сила
endurance | выносливость
flexibility | гибкость
balance | равновесие
coordination | координация
technique | техника выполнения
repetition | повторение упражнения
set | подход
rest | отдыхать
recover | восстанавливаться
pace | темп
breathe | дышать
stay hydrated | поддерживать водный баланс
rest day | день отдыха
make progress | добиваться прогресса
set a goal | поставить цель""",
"films": """film | фильм
TV series | сериал
episode | серия
season | сезон сериала
plot | сюжет
character | персонаж
main character | главный герой
cast | актёрский состав
actor | актёр
director | режиссёр
screenplay | сценарий
scene | сцена
soundtrack | музыка к фильму
subtitles | субтитры
dubbed | дублированный
original version | версия на языке оригинала
trailer | трейлер
release date | дата выхода
sequel | продолжение
adaptation | экранизация
comedy | комедия
thriller | триллер
documentary | документальный фильм
plot twist | неожиданный поворот сюжета
worth watching | стоит посмотреть""",
}


def cards_for(slug: str) -> list[tuple[str, str, str]]:
    rows = []
    for position, line in enumerate(RAW_CARDS[slug].splitlines(), 1):
        term, translation = (part.strip() for part in line.split(" | ", 1))
        rows.append((f"{slug}-{position:02d}", term, translation))
    return rows


def seed_catalog(con: sqlite3.Connection) -> dict[str, int]:
    """Upsert official data without deleting progress, subscriptions or copies."""
    con.execute(
        "INSERT INTO users(telegram_id,name) VALUES(?,?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name",
        (SYSTEM_USER_ID, "Slovo"),
    )
    for slug, title, icon, position in CATEGORIES:
        con.execute(
            "INSERT INTO catalog_categories(slug,title,icon,position) VALUES(?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET title=excluded.title,icon=excluded.icon,position=excluded.position",
            (slug, title, icon, position),
        )
    for slug, category, title, description, icon, position in SETS:
        row = con.execute("SELECT folder_id FROM catalog_sets WHERE slug=?", (slug,)).fetchone()
        if row:
            folder_id = row[0]
            con.execute("UPDATE folders SET name=?,source_lang='en',target_lang='ru' WHERE id=?", (title, folder_id))
            con.execute(
                "UPDATE catalog_sets SET category_slug=?,title=?,description=?,icon=?,position=? WHERE slug=?",
                (category, title, description, icon, position, slug),
            )
        else:
            folder_id = con.execute(
                "INSERT INTO folders(name,owner_id,source_lang,target_lang) VALUES(?,?,?,?)",
                (title, SYSTEM_USER_ID, "en", "ru"),
            ).lastrowid
            con.execute("INSERT OR IGNORE INTO memberships(folder_id,user_id,role) VALUES(?,?,?)", (folder_id, SYSTEM_USER_ID, "owner"))
            con.execute(
                "INSERT INTO catalog_sets(slug,category_slug,folder_id,title,description,icon,position) VALUES(?,?,?,?,?,?,?)",
                (slug, category, folder_id, title, description, icon, position),
            )
        for card_key, term, translation in cards_for(slug):
            card = con.execute("SELECT card_id FROM catalog_cards WHERE card_key=?", (card_key,)).fetchone()
            if card:
                con.execute("UPDATE cards SET term=?,translation=?,folder_id=? WHERE id=?", (term, translation, folder_id, card[0]))
            else:
                card_id = con.execute(
                    "INSERT INTO cards(folder_id,term,translation,created_by) VALUES(?,?,?,?)",
                    (folder_id, term, translation, SYSTEM_USER_ID),
                ).lastrowid
                con.execute(
                    "INSERT INTO catalog_cards(card_key,set_slug,card_id,position) VALUES(?,?,?,?)",
                    (card_key, slug, card_id, int(card_key.rsplit("-", 1)[1])),
                )
    return {"categories": len(CATEGORIES), "sets": len(SETS), "cards": sum(len(cards_for(s[0])) for s in SETS)}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Seed the built-in Slovo catalogue")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/slovo.db"))
    args = parser.parse_args()
    from slovo import DB
    database = DB(args.database)
    with database.conn() as con:
        result = seed_catalog(con)
    print(f"Seeded {result['categories']} categories, {result['sets']} sets and {result['cards']} cards")


if __name__ == "__main__":
    main()
