from fastapi import FastAPI, Form, Request, Response, File, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.encoders import jsonable_encoder
from langchain.chat_models import ChatOpenAI
from langchain.chains import QAGenerationChain
from langchain.text_splitter import TokenTextSplitter
from langchain.docstore.document import Document
from langchain.document_loaders import PyPDFLoader
from langchain.prompts import PromptTemplate
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.chains.summarize import load_summarize_chain
from langchain.chains import RetrievalQA
import os 
import json
import uvicorn
import aiofiles
from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

os.environ["OPENAI_API_KEY"] = ""

def count_pdf_pages(pdf_path):
    try:
        pdf = PdfReader(pdf_path)
        return len(pdf.pages)
    except Exception as e:
        print("Error:", e)
        return None

def file_processing(file_path):
    loader = PyPDFLoader(file_path)
    data = loader.load()

    question_gen = ''
    for page in data:
        question_gen += page.page_content
        
    splitter_ques_gen = TokenTextSplitter(
        model_name = 'gpt-3.5-turbo',
        chunk_size = 10000,
        chunk_overlap = 200
    )

    chunks_ques_gen = splitter_ques_gen.split_text(question_gen)
    document_ques_gen = [Document(page_content=t) for t in chunks_ques_gen]

    splitter_ans_gen = TokenTextSplitter(
        model_name = 'gpt-3.5-turbo',
        chunk_size = 1000,
        chunk_overlap = 100
    )

    document_answer_gen = splitter_ans_gen.split_documents(document_ques_gen)
    return document_ques_gen, document_answer_gen

def llm_pipeline(file_path):
    document_ques_gen, document_answer_gen = file_processing(file_path)

    llm_ques_gen_pipeline = ChatOpenAI(
        temperature = 0.3,
        model = "gpt-3.5-turbo"
    )

    prompt_template = """
    You are an expert at creating questions based on coding materials and documentation.
    Your goal is to prepare a coder or programmer for their exam and coding tests.
    You do this by asking questions about the text below:

    ------------
    {text}
    ------------

    Create questions that will prepare the coders or programmers for their tests.
    Make sure not to lose any important information.

    QUESTIONS:
    """

    PROMPT_QUESTIONS = PromptTemplate(template=prompt_template, input_variables=["text"])

    refine_template = """
    You are an expert at creating practice questions based on coding material and documentation.
    Your goal is to help a coder or programmer prepare for a coding test.
    We have received some practice questions to a certain extent: {existing_answer}.
    We have the option to refine the existing questions or add new ones.
    (only if necessary) with some more context below.
    ------------
    {text}
    ------------

    Given the new context, refine the original questions in English.
    If the context is not helpful, please provide the original questions.
    QUESTIONS:
    """

    REFINE_PROMPT_QUESTIONS = PromptTemplate(
        input_variables=["existing_answer", "text"],
        template=refine_template,
    )

    ques_gen_chain = load_summarize_chain(llm=llm_ques_gen_pipeline, 
                                          chain_type="refine", 
                                          verbose=True, 
                                          question_prompt=PROMPT_QUESTIONS, 
                                          refine_prompt=REFINE_PROMPT_QUESTIONS)

    ques = ques_gen_chain.run(document_ques_gen)

    embeddings = OpenAIEmbeddings()
    vector_store = FAISS.from_documents(document_answer_gen, embeddings)

    llm_answer_gen = ChatOpenAI(temperature=0.1, model="gpt-3.5-turbo")
    ques_list = ques.split("\n")
    filtered_ques_list = [element for element in ques_list if element.endswith('?') or element.endswith('.')]

    answer_generation_chain = RetrievalQA.from_chain_type(llm=llm_answer_gen, 
                                                          chain_type="stuff", 
                                                          retriever=vector_store.as_retriever())

    return answer_generation_chain, filtered_ques_list

def get_pdf(file_path):
    answer_generation_chain, ques_list = llm_pipeline(file_path)
    base_folder = 'static/output/'
    if not os.path.isdir(base_folder):
        os.mkdir(base_folder)
    output_file = base_folder + "QA.pdf"
    
    doc = SimpleDocTemplate(output_file, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    for question in ques_list:
        question_text = f"Question: {question}"
        story.append(Paragraph(question_text, styles['Normal']))
        answer = answer_generation_chain.run(question)
        answer_text = f"Answer: {answer}"
        story.append(Paragraph(answer_text, styles['Normal']))
        story.append(Spacer(1, 12))  # Add space between Q&A pairs

    doc.build(story)
    return output_file

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def chat(request: Request, pdf_file: bytes = File(), filename: str = Form(...)):
    base_folder = 'static/docs/'
    if not os.path.isdir(base_folder):
        os.mkdir(base_folder)
    pdf_filename = os.path.join(base_folder, filename)

    async with aiofiles.open(pdf_filename, 'wb') as f:
        await f.write(pdf_file)
    
    response_data = jsonable_encoder(json.dumps({"msg": 'success', "pdf_filename": pdf_filename}))
    res = Response(response_data)
    return res

@app.post("/analyze")
async def analyze(request: Request, pdf_filename: str = Form(...)):
    output_file = get_pdf(pdf_filename)
    response_data = jsonable_encoder(json.dumps({"output_file": output_file}))
    res = Response(response_data)
    return res

if __name__ == "__main__":
    uvicorn.run("app:app", host='0.0.0.0', port=8000, reload=True)
