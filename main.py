import asyncio
import os
import calendar

import re

import sqlite3

from datetime import date, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F

from aiogram.filters import CommandStart

from aiogram.fsm.context import FSMContext

from aiogram.fsm.state import State, StatesGroup

from aiogram.types import (

    Message, CallbackQuery,

    ReplyKeyboardMarkup, KeyboardButton,

    InlineKeyboardMarkup, InlineKeyboardButton,

)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Environment Variable sozlanmagan.")

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()

# ================= BAZA =================

DB_PATH = os.getenv("DB_PATH", "qarz.db")
db = sqlite3.connect(DB_PATH)

db.execute("""CREATE TABLE IF NOT EXISTS qarzlar (

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER,

    ism TEXT,

    summa INTEGER,

    turi TEXT,

    tolandi INTEGER DEFAULT 0

)""")

# Eski bazaga yangi ustunlarni qo'shamiz (qarzlar yo'qolmaydi)

ustunlar = [qator[1] for qator in db.execute("PRAGMA table_info(qarzlar)")]

for nom in ("sana", "izoh", "telefon", "muddat"):

    if nom not in ustunlar:

        db.execute(f"ALTER TABLE qarzlar ADD COLUMN {nom} TEXT")

# Eski nomlarni yangisiga o'tkazamiz

db.execute("UPDATE qarzlar SET turi = 'mijoz' WHERE turi = 'Men berdim'")

db.execute("UPDATE qarzlar SET turi = 'men' WHERE turi = 'Men oldim'")

db.commit()

# ================= TUGMA YOZUVLARI =================

BTN_QOSH = "➕ Qarz qo'shish"

BTN_ROYXAT = "📋 Qarzlar ro'yxati"

BTN_QISMAN = "💵 Qisman to'lov"

BTN_TOLANDI = "✅ To'landi deb belgilash"

BTN_QIDIR = "🔍 Mijoz qidirish"

BTN_HISOB = "📊 Umumiy hisob"

BTN_BEKOR = "❌ Bekor qilish"

menyu = ReplyKeyboardMarkup(

    keyboard=[

        [KeyboardButton(text=BTN_QOSH), KeyboardButton(text=BTN_ROYXAT)],

        [KeyboardButton(text=BTN_QISMAN), KeyboardButton(text=BTN_TOLANDI)],

        [KeyboardButton(text=BTN_QIDIR), KeyboardButton(text=BTN_HISOB)],

    ],

    resize_keyboard=True,

)

bekor_tugma = ReplyKeyboardMarkup(

    keyboard=[[KeyboardButton(text=BTN_BEKOR)]],

    resize_keyboard=True,

)

def tugma(matn, data):

    return InlineKeyboardButton(text=matn, callback_data=data)

mijoz_tanlash_tugma = InlineKeyboardMarkup(inline_keyboard=[

    [tugma("🔍 Oldingi mijozni qidirish", "mj_qidir")],

    [tugma("🆕 Yangi mijoz", "mj_yangi")],

])

turi_tugma = InlineKeyboardMarkup(inline_keyboard=[

    [tugma("Mijoz qarzdor", "turi_mijoz")],

    [tugma("Men qarzdorman", "turi_men")],

])

muddat_tugma = InlineKeyboardMarkup(inline_keyboard=[

    [tugma("Bugun", "m_0"), tugma("1 hafta", "m_7")],

    [tugma("2 hafta", "m_14"), tugma("1 oy", "m_30")],

    [tugma("📅 Sanani tanlash", "m_kal")],

    [tugma("🚫 Muddatsiz", "m_none")],

])

# ================= YORDAMCHI FUNKSIYALAR =================

OYLAR = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",

         "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr"]

def pul(son):

    return f"{son:,}".replace(",", " ")

def sana_matn(iso):

    return date.fromisoformat(iso).strftime("%d.%m.%Y")

def tozala_ism(matn):

    suzlar = " ".join(matn.split()).split(" ")

    return " ".join(s[:1].upper() + s[1:] for s in suzlar)

async def uzun_yubor(message, matn):

    # Telegram juda uzun xabarni qabul qilmaydi, shuning uchun bo'laklaymiz

    while len(matn) > 3800:

        kesish = matn.rfind("\n\n", 0, 3800)

        if kesish == -1:

            kesish = 3800

        await message.answer(matn[:kesish])

        matn = matn[kesish:].lstrip("\n")

    await message.answer(matn)

def kalendar(yil, oy):

    bugun = date.today()

    oldingi_mumkin = (yil, oy) > (bugun.year, bugun.month)

    oldingi_yil, oldingi_oy = (yil, oy - 1) if oy > 1 else (yil - 1, 12)

    keyingi_yil, keyingi_oy = (yil, oy + 1) if oy < 12 else (yil + 1, 1)

    qatorlar = [[

        tugma("◀" if oldingi_mumkin else " ",

              f"kal_{oldingi_yil}_{oldingi_oy}" if oldingi_mumkin else "noop"),

        tugma(f"{OYLAR[oy - 1]} {yil}", "noop"),

        tugma("▶", f"kal_{keyingi_yil}_{keyingi_oy}"),

    ]]

    qatorlar.append([tugma(h, "noop") for h in ["Du", "Se", "Cho", "Pa", "Ju", "Sha", "Ya"]])

    for hafta in calendar.monthcalendar(yil, oy):

        qator = []

        for kun in hafta:

            if kun == 0:

                qator.append(tugma(" ", "noop"))

            elif date(yil, oy, kun) < bugun:

                qator.append(tugma("·", "noop"))

            else:

                belgi = f"[{kun}]" if date(yil, oy, kun) == bugun else str(kun)

                qator.append(tugma(belgi, f"kun_{yil}_{oy}_{kun}"))

        qatorlar.append(qator)

    qatorlar.append([tugma("🚫 Muddatsiz", "m_none")])

    return InlineKeyboardMarkup(inline_keyboard=qatorlar)

# ---- mijoz qidirish uchun yordamchilar ----

def mijoz_qatorlari(user_id, soz):

    # Ismida shu so'z bor mijozlar va ularning hozirgi qoldig'i

    return db.execute(

        "SELECT ism, COALESCE(SUM(CASE "

        "WHEN tolandi = 0 AND turi = 'mijoz' THEN summa "

        "WHEN tolandi = 0 AND turi = 'men' THEN -summa "

        "ELSE 0 END), 0) "

        "FROM qarzlar WHERE user_id = ? AND ism LIKE ? "

        "GROUP BY ism ORDER BY MAX(id) DESC LIMIT 10",

        (user_id, f"%{soz}%"),

    ).fetchall()

def qoldiq_matn(q):

    if q > 0:

        return f"qarzi {pul(q)}"

    if q < 0:

        return f"siz qarzsiz {pul(-q)}"

    return "qarzi yo'q"

# ================= HOLATLAR (savol-javob bosqichlari) =================

class QarzQosh(StatesGroup):

    tanlash = State()

    qidir = State()

    ism = State()

    summa = State()

    turi = State()

    muddat = State()

    izoh = State()

    telefon = State()

    tasdiq = State()

class QismanTolov(StatesGroup):

    summa = State()

class Qidiruv(StatesGroup):

    ism = State()

# ================= BEKOR QILISH VA START =================

@dp.message(F.text == BTN_BEKOR)

async def bekor(message: Message, state: FSMContext):

    await state.clear()

    await message.answer("Bekor qilindi.", reply_markup=menyu)

@dp.message(CommandStart())

async def start(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "Salom! Men Qarz daftar botiman 👋\nKerakli tugmani tanlang:",

        reply_markup=menyu,

    )

# ================= MENYU TUGMALARI =================

# Eslatma: har bir menyu tugmasi avval boshlangan jarayonni to'xtatadi

# (shu tufayli tugma yozuvi ism bo'lib saqlanib qolmaydi).

@dp.message(F.text == BTN_QOSH)

async def qosh_boshla(message: Message, state: FSMContext):

    await state.clear()

    bor = db.execute(

        "SELECT 1 FROM qarzlar WHERE user_id = ? LIMIT 1",

        (message.from_user.id,),

    ).fetchone()

    if not bor:

        # Hali mijoz yo'q, to'g'ridan-to'g'ri ism so'raymiz

        await state.set_state(QarzQosh.ism)

        await message.answer("Mijoz ismini yozing:", reply_markup=bekor_tugma)

        return

    await state.set_state(QarzQosh.tanlash)

    await message.answer(

        "Yangi qarz qo'shamiz. Bekor qilish uchun pastdagi tugmani bosing.",

        reply_markup=bekor_tugma,

    )

    await message.answer("Mijozni tanlang:", reply_markup=mijoz_tanlash_tugma)

@dp.message(F.text == BTN_ROYXAT)

async def royxat(message: Message, state: FSMContext):

    await state.clear()

    bugun = date.today().isoformat()

    qatorlar = db.execute(

        "SELECT ism, summa, turi, sana, muddat, izoh, telefon FROM qarzlar "

        "WHERE user_id = ? AND tolandi = 0 "

        "ORDER BY CASE WHEN muddat IS NOT NULL AND muddat < ? THEN 0 ELSE 1 END, "

        "CASE WHEN muddat IS NULL THEN 1 ELSE 0 END, muddat, id",

        (message.from_user.id, bugun),

    ).fetchall()

    if not qatorlar:

        await message.answer("Hozircha qarzlar yo'q 🎉")

        return

    matn = "📋 Qarzlaringiz:\n\n"

    for i, (ism, summa, turi, sana, muddat, izoh, tel) in enumerate(qatorlar, start=1):

        if turi == "mijoz":

            matn += f"{i}. {ism} sizga {pul(summa)} so'm qarz\n"

        else:

            matn += f"{i}. Siz {ism}ga {pul(summa)} so'm qarzsiz\n"

        if sana:

            matn += f"   📅 Yozilgan: {sana_matn(sana)}\n"

        if muddat:

            if muddat < bugun:

                matn += f"   ⚠️ Muddati o'tgan: {sana_matn(muddat)}\n"

            else:

                matn += f"   ⏰ Muddat: {sana_matn(muddat)}\n"

        if izoh:

            matn += f"   📝 {izoh}\n"

        if tel:

            matn += f"   📞 {tel}\n"

        matn += "\n"

    await uzun_yubor(message, matn)

@dp.message(F.text == BTN_QISMAN)

async def qisman_royxat(message: Message, state: FSMContext):

    await state.clear()

    qatorlar = db.execute(

        "SELECT id, ism, summa FROM qarzlar WHERE user_id = ? AND tolandi = 0",

        (message.from_user.id,),

    ).fetchall()

    if not qatorlar:

        await message.answer("To'lanmagan qarzlar yo'q 🎉")

        return

    tugmalar = [

        [tugma(f"{ism} — {pul(summa)} so'm", f"qismi_{qarz_id}")]

        for qarz_id, ism, summa in qatorlar

    ]

    await message.answer(

        "Qaysi qarz bo'yicha to'lov bo'ldi? Ustiga bosing:",

        reply_markup=InlineKeyboardMarkup(inline_keyboard=tugmalar),

    )

@dp.message(F.text == BTN_TOLANDI)

async def tolandi_royxat(message: Message, state: FSMContext):

    await state.clear()

    qatorlar = db.execute(

        "SELECT id, ism, summa FROM qarzlar WHERE user_id = ? AND tolandi = 0",

        (message.from_user.id,),

    ).fetchall()

    if not qatorlar:

        await message.answer("To'lanmagan qarzlar yo'q 🎉")

        return

    tugmalar = [

        [tugma(f"{ism} — {pul(summa)} so'm", f"tolandi_{qarz_id}")]

        for qarz_id, ism, summa in qatorlar

    ]

    await message.answer(

        "Qaysi qarz to'landi? Ustiga bosing:",

        reply_markup=InlineKeyboardMarkup(inline_keyboard=tugmalar),

    )

@dp.message(F.text == BTN_QIDIR)

async def qidir_boshla(message: Message, state: FSMContext):

    await state.clear()

    await state.set_state(Qidiruv.ism)

    await message.answer(

        "Mijoz ismini yozing (ismning bir qismi ham bo'ladi):",

        reply_markup=bekor_tugma,

    )

@dp.message(F.text == BTN_HISOB)

async def hisob(message: Message, state: FSMContext):

    await state.clear()

    uid = message.from_user.id

    mijoz = db.execute(

        "SELECT COALESCE(SUM(summa), 0) FROM qarzlar "

        "WHERE user_id = ? AND tolandi = 0 AND turi = 'mijoz'",

        (uid,),

    ).fetchone()[0]

    men = db.execute(

        "SELECT COALESCE(SUM(summa), 0) FROM qarzlar "

        "WHERE user_id = ? AND tolandi = 0 AND turi = 'men'",

        (uid,),

    ).fetchone()[0]

    farq = mijoz - men

    matn = (

        "📊 Umumiy hisob\n\n"

        f"💚 Sizga qarz: {pul(mijoz)} so'm\n"

        f"🔴 Siz qarzsiz: {pul(men)} so'm\n\n"

    )

    if farq >= 0:

        matn += f"Sof hisobda sizga {pul(farq)} so'm qaytishi kerak."

    else:

        matn += f"Sof hisobda siz {pul(-farq)} so'm qaytarishingiz kerak."

    await message.answer(matn)

# ================= QARZ QO'SHISH: MIJOZNI TANLASH =================

async def mijoz_tanlandi(message, state, user_id, ism):

    # Mijoz tanlandi: oldin saqlangan telefon bo'lsa, eslab qolamiz

    qator = db.execute(

        "SELECT telefon FROM qarzlar "

        "WHERE user_id = ? AND ism = ? AND telefon IS NOT NULL AND telefon != '' "

        "ORDER BY id DESC LIMIT 1",

        (user_id, ism),

    ).fetchone()

    await state.update_data(ism=ism, eski_tel=qator[0] if qator else None)

    await state.set_state(QarzQosh.summa)

    await message.answer(

        f"👤 Mijoz: {ism}\n\nQancha so'm? Faqat raqam yozing (masalan: 50000):"

    )

async def natijalar_korsat(message, state, soz, qatorlar, sarlavha):

    soz = tozala_ism(soz)

    await state.update_data(soz=soz, topilgan=[q[0] for q in qatorlar])

    await state.set_state(QarzQosh.qidir)

    tugmalar = [

        [tugma(f"{ism} — {qoldiq_matn(q)}", f"topil_{i}")]

        for i, (ism, q) in enumerate(qatorlar)

    ]

    tugmalar.append([tugma(f"🆕 «{soz}» — yangi mijoz", "yangi_soz")])

    await message.answer(

        sarlavha, reply_markup=InlineKeyboardMarkup(inline_keyboard=tugmalar)

    )

@dp.callback_query(QarzQosh.tanlash, F.data == "mj_qidir")

async def mj_qidir(callback: CallbackQuery, state: FSMContext):

    await state.set_state(QarzQosh.qidir)

    await state.update_data(soz="", topilgan=[])

    await callback.message.edit_text(

        "🔍 Oldingi mijozni qidirish\n\n"

        "Mijoz ismining bir qismini yozing (masalan: Al):"

    )

    await callback.answer()

@dp.callback_query(QarzQosh.tanlash, F.data == "mj_yangi")

async def mj_yangi(callback: CallbackQuery, state: FSMContext):

    await state.set_state(QarzQosh.ism)

    await callback.message.edit_text("🆕 Yangi mijoz\n\nMijoz ismini yozing:")

    await callback.answer()

@dp.message(QarzQosh.qidir)

async def qosh_qidir(message: Message, state: FSMContext):

    soz = " ".join((message.text or "").split())

    if len(soz) < 1 or len(soz) > 40:

        await message.answer("Iltimos, ismning bir qismini yozing (1 dan 40 belgigacha).")

        return

    qatorlar = mijoz_qatorlari(message.from_user.id, soz)

    if qatorlar:

        sarlavha = "Topilgan mijozlar. Birini tanlang:"

    else:

        sarlavha = (

            "Bunday mijoz topilmadi. Boshqacha yozib ko'ring "

            "yoki yangi mijoz qo'shing:"

        )

    await natijalar_korsat(message, state, soz, qatorlar, sarlavha)

@dp.callback_query(QarzQosh.qidir, F.data.startswith("topil_"))

async def qosh_topil(callback: CallbackQuery, state: FSMContext):

    i = int(callback.data.split("_")[1])

    malumot = await state.get_data()

    nomlar = malumot.get("topilgan", [])

    if i >= len(nomlar):

        await callback.answer("Bu tugma eskirgan", show_alert=True)

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await mijoz_tanlandi(callback.message, state, callback.from_user.id, nomlar[i])

    await callback.answer()

@dp.callback_query(QarzQosh.qidir, F.data == "yangi_soz")

async def qosh_yangi_soz(callback: CallbackQuery, state: FSMContext):

    malumot = await state.get_data()

    ism = tozala_ism(malumot.get("soz", ""))

    if len(ism) < 2:

        await callback.answer(

            "Ism juda qisqa. Kamida 2 ta harf yozing.", show_alert=True

        )

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await mijoz_tanlandi(callback.message, state, callback.from_user.id, ism)

    await callback.answer()

# Ismni to'g'ridan-to'g'ri yozsa ham qidiruv bo'ladi

@dp.message(QarzQosh.tanlash)

@dp.message(QarzQosh.ism)

async def qosh_ism(message: Message, state: FSMContext):

    ism = tozala_ism(message.text or "")

    if len(ism) < 2 or len(ism) > 40:

        await message.answer("Iltimos, ismni to'g'ri yozing (2 dan 40 belgigacha).")

        return

    uid = message.from_user.id

    qatorlar = mijoz_qatorlari(uid, ism)

    # Aynan shu ism oldin bo'lgan bo'lsa, o'shani ishlatamiz (Ali / ali aralashmasin)

    for nom, _ in qatorlar:

        if nom.lower() == ism.lower():

            await mijoz_tanlandi(message, state, uid, nom)

            return

    # O'xshash ismlar bo'lsa, so'raymiz

    if qatorlar:

        await natijalar_korsat(

            message, state, ism, qatorlar,

            "Shunga o'xshash mijozlar bor. Birini tanlang yoki yangi mijoz qiling:",

        )

        return

    await mijoz_tanlandi(message, state, uid, ism)

# ================= QARZ QO'SHISH: SUMMA, YO'NALISH, MUDDAT =================

@dp.message(QarzQosh.summa)

async def qosh_summa(message: Message, state: FSMContext):

    matn = (message.text or "").replace(" ", "")

    if not (matn.isascii() and matn.isdigit()) or int(matn) == 0 or len(matn) > 12:

        await message.answer("Iltimos, faqat raqam yozing. Masalan: 50000")

        return

    await state.update_data(summa=int(matn))

    await state.set_state(QarzQosh.turi)

    await message.answer("Kim kimga qarz?", reply_markup=turi_tugma)

@dp.callback_query(QarzQosh.turi, F.data.startswith("turi_"))

async def qosh_turi(callback: CallbackQuery, state: FSMContext):

    turi = callback.data.split("_")[1]

    await state.update_data(turi=turi)

    await state.set_state(QarzQosh.muddat)

    belgi = "Mijoz qarzdor" if turi == "mijoz" else "Men qarzdorman"

    await callback.message.edit_text(f"↔️ {belgi}")

    await callback.message.answer("⏰ Qachongacha qaytariladi?", reply_markup=muddat_tugma)

    await callback.answer()

async def izoh_sora(message: Message, state: FSMContext):

    await state.set_state(QarzQosh.izoh)

    await message.answer(

        "📝 Izoh yozing (masalan: 2 kg guruch, 1 yog'):",

        reply_markup=InlineKeyboardMarkup(

            inline_keyboard=[[tugma("⏭ O'tkazib yuborish", "otkaz_izoh")]]

        ),

    )

async def telefon_sora(message: Message, state: FSMContext):

    m = await state.get_data()

    await state.set_state(QarzQosh.telefon)

    eski = m.get("eski_tel")

    if eski:

        await message.answer(

            f"📞 Saqlangan telefon: {eski}\nShu raqam qoladimi?",

            reply_markup=InlineKeyboardMarkup(inline_keyboard=[

                [tugma("✅ Ha, shu raqam", "tel_ha")],

                [tugma("✏️ O'zgartirish", "tel_ozgar")],

            ]),

        )

        return

    await message.answer(

        "📞 Telefon raqamini yozing (masalan: +998901234567):",

        reply_markup=InlineKeyboardMarkup(

            inline_keyboard=[[tugma("⏭ O'tkazib yuborish", "otkaz_tel")]]

        ),

    )

async def tasdiq_korsat(message: Message, state: FSMContext):

    m = await state.get_data()

    await state.set_state(QarzQosh.tasdiq)

    turi_matn = "Mijoz qarzdor" if m["turi"] == "mijoz" else "Men qarzdorman"

    muddat = sana_matn(m["muddat"]) if m.get("muddat") else "muddatsiz"

    matn = (

        "🧾 Tekshirib oling:\n\n"

        f"👤 Mijoz: {m['ism']}\n"

        f"💰 Summa: {pul(m['summa'])} so'm\n"

        f"↔️ {turi_matn}\n"

        f"📅 Sana: {sana_matn(date.today().isoformat())}\n"

        f"⏰ Muddat: {muddat}\n"

    )

    if m.get("izoh"):

        matn += f"📝 Izoh: {m['izoh']}\n"

    if m.get("telefon"):

        matn += f"📞 Telefon: {m['telefon']}\n"

    matn += "\nSaqlaymizmi?"

    await message.answer(

        matn,

        reply_markup=InlineKeyboardMarkup(

            inline_keyboard=[[tugma("✅ Saqlash", "saqlash"), tugma("❌ Bekor", "bekor_q")]]

        ),

    )

# --- muddat: tayyor tugmalar ---

@dp.callback_query(QarzQosh.muddat, F.data.startswith("m_"))

async def qosh_muddat_tugma(callback: CallbackQuery, state: FSMContext):

    kod = callback.data[2:]

    bugun = date.today()

    if kod == "kal":

        await callback.message.edit_text(

            "📅 Qaytarish sanasini tanlang:",

            reply_markup=kalendar(bugun.year, bugun.month),

        )

        await callback.answer()

        return

    muddat = None if kod == "none" else (bugun + timedelta(days=int(kod))).isoformat()

    await state.update_data(muddat=muddat)

    await callback.message.edit_text(

        "⏰ Muddat: " + (sana_matn(muddat) if muddat else "muddatsiz")

    )

    await izoh_sora(callback.message, state)

    await callback.answer()

# --- muddat: kalendar ---

@dp.callback_query(QarzQosh.muddat, F.data.startswith("kal_"))

async def qosh_kalendar_oy(callback: CallbackQuery):

    _, yil, oy = callback.data.split("_")

    await callback.message.edit_reply_markup(reply_markup=kalendar(int(yil), int(oy)))

    await callback.answer()

@dp.callback_query(QarzQosh.muddat, F.data.startswith("kun_"))

async def qosh_kalendar_kun(callback: CallbackQuery, state: FSMContext):

    _, yil, oy, kun = callback.data.split("_")

    tanlangan = date(int(yil), int(oy), int(kun))

    if tanlangan < date.today():

        await callback.answer("O'tgan sanani tanlab bo'lmaydi", show_alert=True)

        return

    muddat = tanlangan.isoformat()

    await state.update_data(muddat=muddat)

    await callback.message.edit_text(f"⏰ Muddat: {sana_matn(muddat)}")

    await izoh_sora(callback.message, state)

    await callback.answer()

# --- izoh ---

@dp.callback_query(QarzQosh.izoh, F.data == "otkaz_izoh")

async def qosh_izoh_otkaz(callback: CallbackQuery, state: FSMContext):

    await state.update_data(izoh=None)

    await callback.message.edit_text("📝 Izoh: yo'q")

    await telefon_sora(callback.message, state)

    await callback.answer()

@dp.message(QarzQosh.izoh)

async def qosh_izoh(message: Message, state: FSMContext):

    izoh = (message.text or "").strip()

    if not izoh:

        await message.answer("Iltimos, izohni yozing yoki «O'tkazib yuborish» ni bosing.")

        return

    await state.update_data(izoh=izoh[:200])

    await telefon_sora(message, state)

# --- telefon ---

@dp.callback_query(QarzQosh.telefon, F.data == "tel_ha")

async def qosh_tel_ha(callback: CallbackQuery, state: FSMContext):

    m = await state.get_data()

    eski = m.get("eski_tel")

    await state.update_data(telefon=eski)

    await callback.message.edit_text(f"📞 Telefon: {eski}")

    await tasdiq_korsat(callback.message, state)

    await callback.answer()

@dp.callback_query(QarzQosh.telefon, F.data == "tel_ozgar")

async def qosh_tel_ozgar(callback: CallbackQuery, state: FSMContext):

    await state.update_data(eski_tel=None)

    await callback.message.edit_text(

        "📞 Yangi telefon raqamini yozing (masalan: +998901234567):",

        reply_markup=InlineKeyboardMarkup(

            inline_keyboard=[[tugma("⏭ O'tkazib yuborish", "otkaz_tel")]]

        ),

    )

    await callback.answer()

@dp.callback_query(QarzQosh.telefon, F.data == "otkaz_tel")

async def qosh_tel_otkaz(callback: CallbackQuery, state: FSMContext):

    await state.update_data(telefon=None)

    await callback.message.edit_text("📞 Telefon: yo'q")

    await tasdiq_korsat(callback.message, state)

    await callback.answer()

@dp.message(QarzQosh.telefon)

async def qosh_telefon(message: Message, state: FSMContext):

    toza = re.sub(r"[\s\-()]", "", message.text or "")

    if not re.fullmatch(r"\+?\d{9,13}", toza):

        await message.answer(

            "Telefon raqami noto'g'ri. Masalan: +998901234567\n"

            "Yoki «O'tkazib yuborish» ni bosing."

        )

        return

    await state.update_data(telefon=toza)

    await tasdiq_korsat(message, state)

# --- tasdiqlash ---

@dp.callback_query(QarzQosh.tasdiq, F.data == "saqlash")

async def qosh_saqlash(callback: CallbackQuery, state: FSMContext):

    m = await state.get_data()

    db.execute(

        "INSERT INTO qarzlar (user_id, ism, summa, turi, sana, izoh, telefon, muddat) "

        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",

        (

            callback.from_user.id, m["ism"], m["summa"], m["turi"],

            date.today().isoformat(), m.get("izoh"), m.get("telefon"), m.get("muddat"),

        ),

    )

    db.commit()

    await state.clear()

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer("✅ Qarz saqlandi!", reply_markup=menyu)

    await callback.answer()

@dp.callback_query(QarzQosh.tasdiq, F.data == "bekor_q")

async def qosh_bekor(callback: CallbackQuery, state: FSMContext):

    await state.clear()

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer("Bekor qilindi. Qarz saqlanmadi.", reply_markup=menyu)

    await callback.answer()

# ================= TO'LANDI DEB BELGILASH =================

@dp.callback_query(F.data.startswith("tolandi_"))

async def tolandi_bosildi(callback: CallbackQuery):

    qarz_id = int(callback.data.split("_")[1])

    db.execute(

        "UPDATE qarzlar SET tolandi = 1 WHERE id = ? AND user_id = ?",

        (qarz_id, callback.from_user.id),

    )

    db.commit()

    await callback.message.edit_text("✅ Qarz to'langan deb belgilandi!")

    await callback.answer()

# ================= QISMAN TO'LOV =================

@dp.callback_query(F.data.startswith("qismi_"))

async def qisman_tanlandi(callback: CallbackQuery, state: FSMContext):

    qarz_id = int(callback.data.split("_")[1])

    qator = db.execute(

        "SELECT ism, summa FROM qarzlar WHERE id = ? AND user_id = ? AND tolandi = 0",

        (qarz_id, callback.from_user.id),

    ).fetchone()

    if not qator:

        await callback.answer("Bu qarz topilmadi", show_alert=True)

        return

    await state.update_data(qarz_id=qarz_id)

    await state.set_state(QismanTolov.summa)

    await callback.message.answer(

        f"{qator[0]}: qolgan qarz {pul(qator[1])} so'm.\n"

        "Necha so'm to'landi? Raqam yozing:",

        reply_markup=bekor_tugma,

    )

    await callback.answer()

@dp.message(QismanTolov.summa)

async def qisman_summa(message: Message, state: FSMContext):

    matn = (message.text or "").replace(" ", "")

    if not (matn.isascii() and matn.isdigit()) or int(matn) == 0 or len(matn) > 12:

        await message.answer("Iltimos, 0 dan katta raqam yozing. Masalan: 50000")

        return

    tolov = int(matn)

    malumot = await state.get_data()

    qarz_id = malumot["qarz_id"]

    qator = db.execute(

        "SELECT summa FROM qarzlar WHERE id = ? AND user_id = ? AND tolandi = 0",

        (qarz_id, message.from_user.id),

    ).fetchone()

    if not qator:

        await state.clear()

        await message.answer("Bu qarz topilmadi.", reply_markup=menyu)

        return

    qolgan = qator[0]

    if tolov > qolgan:

        await message.answer(

            f"Qolgan qarz {pul(qolgan)} so'm. Shundan kam yoki teng summa yozing."

        )

        return

    yangi = qolgan - tolov

    if yangi == 0:

        db.execute("UPDATE qarzlar SET summa = 0, tolandi = 1 WHERE id = ?", (qarz_id,))

        javob = "✅ Qarz to'liq to'landi!"

    else:

        db.execute("UPDATE qarzlar SET summa = ? WHERE id = ?", (yangi, qarz_id))

        javob = f"✅ Qabul qilindi. Qolgan qarz: {pul(yangi)} so'm"

    db.commit()

    await state.clear()

    await message.answer(javob, reply_markup=menyu)

# ================= MIJOZ QIDIRISH (menyudagi tugma) =================

@dp.message(Qidiruv.ism)

async def qidir_natija(message: Message, state: FSMContext):

    soz = (message.text or "").strip()

    qatorlar = db.execute(

        "SELECT ism, summa, turi, telefon FROM qarzlar "

        "WHERE user_id = ? AND tolandi = 0 AND ism LIKE ?",

        (message.from_user.id, f"%{soz}%"),

    ).fetchall()

    await state.clear()

    if not soz or not qatorlar:

        await message.answer("Bunday ismli qarz topilmadi.", reply_markup=menyu)

        return

    matn = f"🔍 \"{soz}\" bo'yicha natija:\n\n"

    jami = 0

    for ism, summa, turi, tel in qatorlar:

        if turi == "mijoz":

            matn += f"• {ism} sizga {pul(summa)} so'm qarz\n"

            jami += summa

        else:

            matn += f"• Siz {ism}ga {pul(summa)} so'm qarzsiz\n"

            jami -= summa

        if tel:

            matn += f"  📞 {tel}\n"

    if jami >= 0:

        matn += f"\nJami: sizga {pul(jami)} so'm qaytishi kerak."

    else:

        matn += f"\nJami: siz {pul(-jami)} so'm qaytarishingiz kerak."

    await message.answer(matn, reply_markup=menyu)

# ================= BO'SH VA ESKIRGAN TUGMALAR =================

@dp.callback_query(F.data == "noop")

async def bosh_tugma(callback: CallbackQuery):

    await callback.answer()

@dp.callback_query()

async def eskirgan_tugma(callback: CallbackQuery):

    await callback.answer(

        "Bu tugma eskirgan. Iltimos, menyudan qaytadan boshlang.", show_alert=True

    )

# ================= AIOHTTP HEALTH SERVER =================

async def health_check(request: web.Request) -> web.Response:
    """UptimeRobot va Render health check uchun oddiy javob."""
    return web.Response(text="OK", status=200)

async def run_web_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    print(f"Health server 0.0.0.0:{port} da ishga tushdi.")

    try:
        # Server bot polling bilan bir asyncio event loop ichida ishlaydi.
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

# ================= ISHGA TUSHIRISH =================

async def main():
    print("Bot ishga tushmoqda: polling + aiohttp health server...")
    await asyncio.gather(
        dp.start_polling(bot),
        run_web_server(),
    )

if __name__ == "__main__":
    asyncio.run(main())
