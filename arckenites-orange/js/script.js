/* ============================================================
   ARCKENITES — HOME PAGE SCRIPT
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {

  /* ---------- AOS Init ---------- */
  if (window.AOS) {
    AOS.init({ duration: 700, once: true, offset: 60 });
  }

  /* ---------- Loader ---------- */
  const loader = document.getElementById('loader');
  setTimeout(() => {
    if (loader) loader.classList.add('hidden');
  }, 300);

  /* ---------- Footer year ---------- */
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ---------- Sticky navbar shrink ---------- */
  const navbar = document.getElementById('mainNavbar');
  window.addEventListener('scroll', () => {
    if (window.scrollY > 40) navbar.classList.add('scrolled');
    else navbar.classList.remove('scrolled');

    const backToTop = document.getElementById('backToTop');
    if (window.scrollY > 400) backToTop.classList.add('show');
    else backToTop.classList.remove('show');
  });

  /* ---------- Back to top ---------- */
  document.getElementById('backToTop').addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  /* ---------- Search toggle ---------- */
  const searchToggle = document.getElementById('searchToggle');
  const searchBar = document.getElementById('searchBar');
  const searchClose = document.getElementById('searchClose');
  const searchInput = document.getElementById('courseSearchInput');

  searchToggle.addEventListener('click', () => {
    searchBar.classList.toggle('open');
    if (searchBar.classList.contains('open')) setTimeout(() => searchInput.focus(), 300);
  });
  searchClose.addEventListener('click', () => searchBar.classList.remove('open'));

  /* ---------- Course search (redirects to courses page with query) ---------- */
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && searchInput.value.trim()) {
      window.location.href = `courses.html?search=${encodeURIComponent(searchInput.value.trim())}`;
    }
  });

  /* ---------- Cookie consent ---------- */
  const cookieBanner = document.getElementById('cookieBanner');
  const cookieAccept = document.getElementById('cookieAccept');
  setTimeout(() => cookieBanner.classList.add('show'), 1200);
  cookieAccept.addEventListener('click', () => cookieBanner.classList.remove('show'));

  /* ---------- Animated counters ---------- */
  const counters = document.querySelectorAll('.counter');
  const animateCounter = (el) => {
    const target = +el.getAttribute('data-target');
    const duration = 1500;
    const start = performance.now();

    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      const value = Math.floor(progress * target);
      el.textContent = value + (target >= 100 ? '+' : '%');
      if (target < 100) el.textContent = value + '%';
      else el.textContent = value + '+';
      if (progress < 1) requestAnimationFrame(step);
      else el.textContent = target + (target < 100 ? '%' : '+');
    };
    requestAnimationFrame(step);
  };

  const counterObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        animateCounter(entry.target);
        counterObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });

  counters.forEach((c) => counterObserver.observe(c));

  /* ---------- Newsletter form (demo submit) ---------- */
  const newsletterForm = document.getElementById('newsletterForm');
  newsletterForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const input = newsletterForm.querySelector('input');
    input.value = '';
    input.placeholder = "Thanks! You're subscribed 🎉";
  });

  /* ---------- Our Programs data + render ---------- */
  const courses = [
    { icon: 'fa-solid fa-certificate', title: 'Official Certification Program', desc: 'Globally recognized certifications backed by industry-aligned assessments.', link: 'certification-program.html' },
    { icon: 'fa-solid fa-building', title: 'Corporate Training Program', desc: 'Customized upskilling programs designed for corporate teams and enterprises.', link: 'about.html' },
    { icon: 'fa-solid fa-school', title: 'Institutional Program', desc: 'Partnered training programs delivered directly within colleges and institutions.', link: 'about.html' },
    { icon: 'fa-solid fa-briefcase', title: 'Placement Training Program', desc: 'Interview prep, resume building and placement support until you land the job.', link: 'about.html' },
    { icon: 'fa-solid fa-chalkboard-user', title: 'Arckenites Trainers Program', desc: 'A train-the-trainer track that builds the next generation of Arckenites mentors.', link: 'about.html' },
    { icon: 'fa-solid fa-laptop-code', title: 'Internship Program', desc: 'Hands-on internships with real projects to kickstart your IT career.', link: 'about.html' }
  ];

  const grid = document.getElementById('courseGrid');
  if (grid) {
    grid.innerHTML = courses.map((c, i) => `
      <div class="col-md-6 col-lg-4" data-aos="fade-up" data-aos-delay="${(i % 3) * 100}">
        <div class="course-card">
          <div class="course-icon"><i class="${c.icon}"></i></div>
          <h5>${c.title}</h5>
          <p>${c.desc}</p>
          <a href="${c.link}" class="btn-primary-outline">Explore More</a>
        </div>
      </div>
    `).join('');
  }

});

/* ============================================================
   INNER PAGES — SHARED BEHAVIOUR
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {

  /* ---------- Full course catalog (Courses listing page) ---------- */
  const fullCourses = [
    { title:'AWS Cloud', cat:'cloud', icon:'fa-brands fa-aws', img:'https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=600&q=70', desc:'Master core AWS services and architect scalable cloud solutions.', duration:'10 Weeks', level:'Advanced', fee:'₹28,000' },
    { title:'Microsoft Azure', cat:'cloud', icon:'fa-solid fa-cloud', img:'https://images.unsplash.com/photo-1667372393119-3d4c48d07fc9?auto=format&fit=crop&w=600&q=70', desc:'Deploy, manage and secure workloads on Microsoft Azure.', duration:'10 Weeks', level:'Advanced', fee:'₹28,000' },
    { title:'DevOps Engineering', cat:'cloud', icon:'fa-solid fa-infinity', img:'https://images.unsplash.com/photo-1667372459677-a1e424024f56?auto=format&fit=crop&w=600&q=70', desc:'CI/CD, containers and automation with real deployment pipelines.', duration:'12 Weeks', level:'Intermediate', fee:'₹32,000' },
    { title:'Java Full Stack', cat:'development', icon:'fa-brands fa-java', img:'https://images.unsplash.com/photo-1517694712202-14dd9538aa97?auto=format&fit=crop&w=600&q=70', desc:'Build production-grade apps with Java, Spring Boot and Angular.', duration:'16 Weeks', level:'Advanced', fee:'₹42,000' },
    { title:'MERN Stack', cat:'development', icon:'fa-brands fa-node-js', img:'https://images.unsplash.com/photo-1633356122544-f134324a6cee?auto=format&fit=crop&w=600&q=70', desc:'MongoDB, Express, React and Node — build modern web apps.', duration:'14 Weeks', level:'Intermediate', fee:'₹38,000' },
    { title:'Python Programming', cat:'development', icon:'fa-brands fa-python', img:'https://images.unsplash.com/photo-1526379879527-8559ecfcaec0?auto=format&fit=crop&w=600&q=70', desc:'From fundamentals to building real applications with Python.', duration:'8 Weeks', level:'Beginner', fee:'₹18,000' },
    { title:'Data Analytics', cat:'data', icon:'fa-solid fa-chart-line', img:'https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=600&q=70', desc:'Turn raw data into business decisions using SQL, Excel and Python.', duration:'10 Weeks', level:'Beginner', fee:'₹24,000' },
    { title:'Power BI', cat:'data', icon:'fa-solid fa-chart-pie', img:'https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=600&q=70', desc:'Design dashboards and reports that drive business insight.', duration:'6 Weeks', level:'Beginner', fee:'₹15,000' },
    { title:'AI & Machine Learning', cat:'ai', icon:'fa-solid fa-brain', img:'https://images.unsplash.com/photo-1620712943543-bcc4688e7485?auto=format&fit=crop&w=600&q=70', desc:'Build and deploy ML models using Python and real datasets.', duration:'14 Weeks', level:'Advanced', fee:'₹40,000' },
    { title:'Cyber Security', cat:'security', icon:'fa-solid fa-shield-halved', img:'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=600&q=70', desc:'Network security, ethical hacking and security operations.', duration:'12 Weeks', level:'Intermediate', fee:'₹30,000' },
    { title:'Software Testing', cat:'security', icon:'fa-solid fa-vial', img:'https://images.unsplash.com/photo-1517430816045-df4b7de11d1d?auto=format&fit=crop&w=600&q=70', desc:'Manual and automation testing with Selenium and real projects.', duration:'8 Weeks', level:'Beginner', fee:'₹16,000' },
    { title:'Linux Administration', cat:'cloud', icon:'fa-brands fa-linux', img:'https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=600&q=70', desc:'Administer, secure and troubleshoot enterprise Linux systems.', duration:'8 Weeks', level:'Intermediate', fee:'₹18,000' }
  ];

  const allGrid = document.getElementById('allCoursesGrid');
  if (allGrid) {
    const renderCourses = (list) => {
      allGrid.innerHTML = list.map(c => `
        <div class="col-md-6 col-lg-4 course-item" data-cat="${c.cat}">
          <div class="course-list-card">
            <img src="${c.img}" alt="${c.title} course">
            <div class="body">
              <span class="badge-level">${c.level}</span>
              <h5>${c.title}</h5>
              <p>${c.desc}</p>
              <div class="course-meta">
                <span><i class="fa-regular fa-clock"></i> ${c.duration}</span>
                <span class="price">${c.fee}</span>
              </div>
              <div class="actions">
                <a href="course-details.html" class="btn btn-primary-outline">Syllabus</a>
                <a href="free-demo.html" class="btn btn-accent">Enroll</a>
              </div>
            </div>
          </div>
        </div>
      `).join('');
    };
    renderCourses(fullCourses);

    const pills = document.querySelectorAll('.filter-pills button');
    pills.forEach(pill => {
      pill.addEventListener('click', () => {
        pills.forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        const cat = pill.getAttribute('data-filter');
        renderCourses(cat === 'all' ? fullCourses : fullCourses.filter(c => c.cat === cat));
      });
    });

    // Prefill from ?search= query param
    const params = new URLSearchParams(window.location.search);
    const q = params.get('search');
    if (q) {
      const filtered = fullCourses.filter(c => c.title.toLowerCase().includes(q.toLowerCase()));
      renderCourses(filtered.length ? filtered : fullCourses);
    }
  }

  /* ---------- Generic form submit stubs (Free Demo, Corporate, Careers) ---------- */
  document.querySelectorAll('.demo-form, .inquiry-form, .apply-form').forEach(form => {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      const originalText = btn.textContent;
      btn.textContent = 'Submitted ✓';
      btn.disabled = true;
      setTimeout(() => { btn.textContent = originalText; btn.disabled = false; form.reset(); }, 2500);
    });
  });

  /* ---------- Contact form: live submission to the backend ---------- */
  const contactForm = document.getElementById('contactForm');
  if (contactForm && window.ArckAPI) {
    const contactErrorBox = document.getElementById('contactFormError');
    const contactSubmitBtn = document.getElementById('contactFormSubmitBtn');

    contactForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      contactErrorBox.style.display = 'none';

      const body = {
        full_name: document.getElementById('contactFullName').value.trim(),
        phone: document.getElementById('contactPhone').value.trim(),
        email: document.getElementById('contactEmail').value.trim(),
        subject: document.getElementById('contactSubject').value.trim() || null,
        message: document.getElementById('contactMessage').value.trim(),
      };

      const originalText = contactSubmitBtn.textContent;
      contactSubmitBtn.disabled = true;

      try {
        await ArckAPI.request('/contact/enquiries', { method: 'POST', body, auth: false });
        contactSubmitBtn.textContent = 'Submitted ✓';
        setTimeout(() => { contactSubmitBtn.textContent = originalText; contactSubmitBtn.disabled = false; contactForm.reset(); }, 2500);
      } catch (err) {
        contactErrorBox.textContent = err.detail || 'Could not send your message. Please try again.';
        contactErrorBox.style.display = 'block';
        contactSubmitBtn.textContent = originalText;
        contactSubmitBtn.disabled = false;
      }
    });
  }

});
